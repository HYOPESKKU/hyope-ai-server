import csv
import json
import os
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from clawops import AsyncClawOps
from clawops.agent import ClawOpsAgent

from llm_analyzer import analyze_with_llm
from phq_classifier import classify_phq_item, identify_phq_item
from realtime_session import CourseRealtimeSession
from schemas import AnalyzeCallRequest, CallLogEntry

VALID_TYPES = {"MEDICATION", "MEAL"}
VALID_SLOTS = {"BREAKFAST", "LUNCH", "DINNER"}

SYSTEM_PROMPT = """당신은 어르신의 건강을 돌보는 친절한 AI 상담 전화 도우미입니다.

# 핵심 규칙
- 반드시 한 번에 질문 하나만 하세요. 어르신이 답하면 공감 한 마디 후 다음 질문으로 넘어가세요.
- 질문을 두 개 이상 한 문장에 묶지 마세요. (예: "약은 드셨나요? 식사는요?" 금지)
- 답변을 들은 후 "아, 그러셨군요." / "다행이네요." / "많이 힘드셨겠어요." 같은 짧은 공감을 먼저 하세요.

# 진행 순서 (반드시 이 순서대로, 하나씩)
1. 인사: "안녕하세요, 효프 건강 확인 전화입니다. 잘 지내고 계세요?"
2. 지역 확인: "어디에 살고 계세요?"
   ↳ 답변을 받으면 반드시 check_weather_alert(region) 도구를 호출하세요 — 답을 듣고도
     호출하지 않고 넘어가면 안 됩니다. should_mention이 true면 공감 표현 다음에 advice
     내용을 자연스럽게 한 문장 덧붙이세요. has_alert 또는 should_mention이 false면
     날씨 이야기는 꺼내지 말고 바로 다음 순서로 넘어가세요.
3. 복약 확인: "오늘 약은 드셨나요?"
4. 식사 확인: "오늘 식사는 하셨나요?"
5. 신체 불편 확인: "몸이 어디 아프거나 불편하신 데는 없으세요?"
6. 마무리: 아래 중 하나를 무작위로 선택해 따뜻하게 마무리하세요.
   - "오늘도 건강하게 잘 지내셨네요, 내일도 좋은 하루 되세요!"
   - "어르신 덕분에 저도 기분이 좋아졌어요, 항상 건강하세요!"
   - "오늘 통화해서 정말 반가웠어요, 맛있는 거 많이 드세요!"
   - "어르신 목소리 듣고 나니 저도 힘이 나요, 좋은 하루 되세요!"
   - "오늘 하루도 수고 많으셨어요, 푹 쉬시고 내일 또 통화해요!"

# 도구(tool) 사용 규칙
- find_nearby_welfare_service(need_type, location): 어르신이 돌봄·요양 등 실제 도움이
  필요하다는 구체적 요청을 했을 때만 호출하세요 (단순한 푸념과 구분할 것). 통화 중에는
  검색된 기관명·전화번호를 절대 직접 읽어주지 말고, "제가 확인해서 보호자분께 알려드릴게요"
  정도로만 안내한 뒤 원래 진행 순서로 돌아가세요.

# 추가 규칙
- 어르신이 걱정되는 이야기를 하면 자연스럽게 한 번 더 여쭤봐도 됩니다. 단, 그 후엔 다음 순서로 넘어가세요.
- 말투는 항상 따뜻하고 천천히, 쉬운 단어로 말하세요.
- 절대 의학적 진단을 내리지 마세요."""

# call_id → {recipient_id, guardian_phone, started_at, segments}
_call_registry: dict[str, dict] = {}

# 이 Agent는 통화를 한 번에 하나씩만 처리한다(ClawOpsAgent가 session 인스턴스를 콜 간에
# 공유하는 구조라 동시 통화를 가정하지 않음) — 그래서 "지금 진행 중인 통화가 뭔지"를
# 모델이 tool 인자로 넘기게 하지 않고 여기 전역 변수로 추적한다.
_active_call_id: str | None = None

agent = ClawOpsAgent(
    from_=os.getenv("CLAWOPS_FROM_NUMBER", ""),
    session=CourseRealtimeSession(
        system_prompt=SYSTEM_PROMPT,
        voice="coral",
        language="ko",
        turn_detection={"type": "semantic_vad", "eagerness": "low"},
        greeting=True,
    ),
)

_sms_client = AsyncClawOps(
    api_key=os.getenv("CLAWOPS_API_KEY"),
    account_id=os.getenv("CLAWOPS_ACCOUNT_ID"),
)


# @agent.tool
# async def notify_guardian(reason: str) -> str:
#     """어르신 통화 중 건강·정서적으로 걱정되는 신호가 감지되었을 때 보호자에게 즉시
#     SMS로 알립니다. 통화가 끝날 때까지 기다리지 말고, 위험 신호를 들은 즉시 호출하세요.
#
#     Args:
#         reason: 무엇이 걱정되는지 한두 문장으로 (보호자에게 그대로 전달됨).
#     """
#     global _active_call_id
#     info = _call_registry.get(_active_call_id or "")
#     guardian_phone = (info or {}).get("guardian_phone", "")
#     if not guardian_phone:
#         return "실패: 보호자 연락처가 등록되어 있지 않습니다."
#
#     try:
#         await _sms_client.messages.create(
#             to=guardian_phone,
#             from_=os.getenv("CLAWOPS_FROM_NUMBER", ""),
#             body=f"[효프] 통화 중 확인이 필요한 내용이 있습니다: {reason}",
#             type="sms",
#         )
#         print(f"[TOOL] notify_guardian → {guardian_phone}: {reason}")
#         return "성공: 보호자에게 문자를 보냈습니다."
#     except Exception as e:
#         print(f"[TOOL] notify_guardian 실패: {e}")
#         return f"실패: {e}"


# ── 기상청 단기예보 조회서비스(2.0) getVilageFcst (data.go.kr) ─────────────────
# https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst
# 특보(주의보/경보) 텍스트를 직접 주지 않고 기온·강수형태 등 수치 예보값만 주므로,
# 폭염/한파/비/눈 여부는 이 서버가 임계값 기준으로 직접 판단한다.
KMA_WEATHER_API_URL = os.getenv(
    "WEATHER_ALERT_API_URL",
    "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst",
)
KMA_WEATHER_API_KEY = os.getenv("WEATHER_ALERT_API_KEY")

# region(수신자 등록 주소) → (nx, ny) 예보지점 격자좌표 매핑. 별첨 엑셀자료 기준
# 좌표를 JSON으로 채워 넣는다 (예: '{"서울":[60,127],"부산":[98,76]}').
# 아래 기본값은 각 지역 대표지점의 널리 알려진 좌표이지만 별첨 엑셀로 검증 필요.
_KMA_NXNY_BY_REGION: dict[str, tuple[int, int]] = {
    key: tuple(coord)
    for key, coord in json.loads(
        os.getenv(
            "KMA_NXNY_MAP",
            '{"서울":[60,127],"인천":[55,124],"경기":[60,120],"강원":[73,134],'
            '"충북":[69,106],"충남":[68,100],"세종":[66,103],"대전":[67,100],'
            '"전북":[63,89],"전남":[51,67],"광주":[58,74],"경북":[89,91],'
            '"경남":[91,77],"대구":[89,90],"울산":[102,84],"부산":[98,76],"제주":[52,38]}',
        )
    ).items()
}

# 같은 특보를 매 통화마다 언급하면 잔소리로 느껴져 무시되므로(피로도), 수신자별로
# 특보 종류당 마지막 안내 시각을 기억해 N일 이내 재언급을 막는다.
_WEATHER_ALERT_COOLDOWN_DAYS = 3
_weather_alert_last_notified: dict[int, dict[str, datetime]] = {}

_VILAGE_FCST_BASE_TIMES = ("0200", "0500", "0800", "1100", "1400", "1700", "2000", "2300")
_TMP_HEAT_THRESHOLD = 33.0  # 폭염특보 발효 기준(일 최고기온 33℃ 이상)과 동일한 임계값
_TMP_COLD_THRESHOLD = -12.0  # 한파특보 발효 기준(아침 최저기온 -12℃ 이하)과 동일한 임계값

_PTY_ADVICE: dict[str, dict[str, str]] = {
    "1": {"type": "비", "advice": "우산을 챙기시고 미끄러운 길 조심하세요."},
    "2": {"type": "비/눈", "advice": "우산을 챙기시고 눈길 미끄럼에도 주의하세요."},
    "3": {"type": "눈", "advice": "눈길 운전과 보행 시 미끄러짐에 주의하세요."},
    "4": {"type": "소나기", "advice": "우산을 챙기시고 갑작스러운 소나기에 대비하세요."},
}


def _resolve_nxny(region: str) -> tuple[int, int] | None:
    for key, coord in _KMA_NXNY_BY_REGION.items():
        if key in region:
            return coord
    return None


def _find_dict_with_key(obj: Any, key: str) -> dict[str, Any] | None:
    """data.go.kr 응답이 response/body/items로 감싼 구조라 어느 깊이에 있든
    해당 키를 가진 dict를 재귀적으로 찾는다."""
    if isinstance(obj, dict):
        if key in obj:
            return obj
        for value in obj.values():
            found = _find_dict_with_key(value, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_dict_with_key(item, key)
            if found is not None:
                return found
    return None


def _extract_items(data: Any) -> list[dict[str, Any]]:
    found = _find_dict_with_key(data, "item")
    if not found:
        return []
    item = found["item"]
    if isinstance(item, list):
        return item
    if isinstance(item, dict):
        return [item]
    return []


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _latest_vilage_fcst_base(now: datetime | None = None) -> tuple[str, str]:
    """단기예보 발표시각(02·05·08·11·14·17·20·23시, 매시 10분 이후 제공) 중
    현재 이용 가능한 가장 최근 base_date/base_time을 계산한다."""
    now = now or datetime.now()
    candidates: list[tuple[datetime, str, str]] = []
    for offset_days in (0, 1):
        day = now - timedelta(days=offset_days)
        for base_time in _VILAGE_FCST_BASE_TIMES:
            available_at = day.replace(hour=int(base_time[:2]), minute=10, second=0, microsecond=0)
            if available_at <= now:
                candidates.append((available_at, day.strftime("%Y%m%d"), base_time))
    if not candidates:
        # 자정 근처라 후보가 전혀 없으면 전날 23시 발표를 사용
        yesterday = now - timedelta(days=1)
        return yesterday.strftime("%Y%m%d"), "2300"
    candidates.sort(key=lambda c: c[0])
    _, base_date, base_time = candidates[-1]
    return base_date, base_time


def _derive_alert_from_items(items: list[dict[str, Any]]) -> dict[str, str] | None:
    temps = [
        v for i in items if i.get("category") in ("TMP", "TMX", "TMN")
        if (v := _safe_float(i.get("fcstValue"))) is not None
    ]
    if temps and max(temps) >= _TMP_HEAT_THRESHOLD:
        return {"type": "폭염", "advice": "물을 자주 드시고 낮에는 외출을 자제하세요."}
    if temps and min(temps) <= _TMP_COLD_THRESHOLD:
        return {"type": "한파", "advice": "외출하실 때 옷을 두껍게 입으시고 보일러를 꺼뜨리지 마세요."}

    pty_items = sorted(
        (i for i in items if i.get("category") == "PTY" and i.get("fcstValue") not in ("0", None)),
        key=lambda i: (i.get("fcstDate", ""), i.get("fcstTime", "")),
    )
    if pty_items:
        return _PTY_ADVICE.get(pty_items[0]["fcstValue"])

    return None


async def _fetch_weather_alert(region: str) -> dict[str, str] | None:
    if not KMA_WEATHER_API_KEY:
        print("[TOOL] WEATHER_ALERT_API_KEY가 설정되어 있지 않습니다.")
        return None

    nxny = _resolve_nxny(region)
    if not nxny:
        print(f"[TOOL] '{region}'에 대한 nx/ny 매핑을 찾을 수 없습니다 (KMA_NXNY_MAP 확인).")
        return None
    nx, ny = nxny

    base_date, base_time = _latest_vilage_fcst_base()
    params = {
        "serviceKey": KMA_WEATHER_API_KEY,
        "numOfRows": 1000,
        "pageNo": 1,
        "dataType": "JSON",
        "base_date": base_date,
        "base_time": base_time,
        "nx": nx,
        "ny": ny,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(KMA_WEATHER_API_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        print(f"[TOOL] 기상청 API 호출 실패: {e}")
        return None

    header = _find_dict_with_key(data, "resultCode")
    if not header or header.get("resultCode") not in (None, "00"):
        print(f"[TOOL] 기상청 API 오류 응답: {(header or {}).get('resultMsg')}")
        return None

    return _derive_alert_from_items(_extract_items(data))


@agent.tool
async def check_weather_alert(region: str) -> str:
    """수신자 거주 지역에 폭염·한파 특보가 발효 중인지 확인합니다. 통화 초반 안부를
    묻거나 날씨 이야기가 나올 때 호출하세요. should_mention이 false이면 최근에 이미
    안내한 특보이니 이번 통화에서는 절대 다시 언급하지 마세요.

    Args:
        region: 수신자가 거주하는 지역명 (예: "서울", "강원 춘천").
    """
    info = _call_registry.get(_active_call_id or "")
    recipient_id = (info or {}).get("recipient_id")

    alert = await _fetch_weather_alert(region)
    if not alert:
        return json.dumps({"has_alert": False}, ensure_ascii=False)

    alert_type = alert["type"]
    now = datetime.now()
    history = _weather_alert_last_notified.setdefault(recipient_id, {}) if recipient_id is not None else {}
    last_notified = history.get(alert_type)
    if last_notified and (now - last_notified) < timedelta(days=_WEATHER_ALERT_COOLDOWN_DAYS):
        return json.dumps({"has_alert": True, "should_mention": False}, ensure_ascii=False)

    history[alert_type] = now
    return json.dumps({
        "has_alert": True,
        "should_mention": True,
        "type": alert_type,
        "advice": alert["advice"],
    }, ensure_ascii=False)


# ── 노인돌봄서비스 기관 현황 (data.go.kr 15004317, CSV 파일데이터) ─────────────────
# https://www.data.go.kr/data/15004317/fileData.do 에서 다운받아 WELFARE_CSV_PATH
# 경로에 두면 된다. 컬럼: 연번,시도,시군구,기관명,주소,기관 대표전화,위수탁기간,
# 시설유형구분,특화서비스,응급안전안심서비스,방문요양서비스,재가노인복지서비스,
# 사회서비스원소속,사회서비스형 노인일자리 파견이용
WELFARE_CSV_PATH = os.getenv("WELFARE_CSV_PATH", "data/welfare_services.csv")

_WELFARE_LOCATION_COLUMNS = ("시도", "시군구")
_WELFARE_NEED_TYPE_COLUMNS = ("시설유형구분", "특화서비스")

_welfare_services_cache: list[dict[str, str]] | None = None


def _load_welfare_services() -> list[dict[str, str]]:
    global _welfare_services_cache
    if _welfare_services_cache is not None:
        return _welfare_services_cache

    path = Path(WELFARE_CSV_PATH)
    if not path.exists():
        print(f"[TOOL] 복지서비스 CSV를 찾을 수 없습니다: {path} (WELFARE_CSV_PATH 확인)")
        _welfare_services_cache = []
        return _welfare_services_cache

    rows: list[dict[str, str]] = []
    for encoding in ("utf-8-sig", "cp949"):
        try:
            with path.open(encoding=encoding, newline="") as f:
                rows = list(csv.DictReader(f))
            break
        except UnicodeDecodeError:
            continue

    _welfare_services_cache = rows
    return rows


def _row_matches_location(row: dict[str, str], location: str) -> bool:
    for col in _WELFARE_LOCATION_COLUMNS:
        value = (row.get(col) or "").strip()
        if value and (value in location or location in value):
            return True
    return False


def _row_matches_need_type(row: dict[str, str], need_type: str) -> bool:
    return any(need_type in (row.get(col) or "") for col in _WELFARE_NEED_TYPE_COLUMNS)


def _lookup_welfare_services(need_type: str, location: str) -> list[dict[str, str]]:
    rows = _load_welfare_services()
    matches = [
        row for row in rows
        if _row_matches_location(row, location) and _row_matches_need_type(row, need_type)
    ]
    return matches[:3]


@agent.tool
async def find_nearby_welfare_service(need_type: str, location: str) -> str:
    """어르신이 실제로 도움이 필요하다는 구체적 요청을 했을 때만 호출하세요 — 단순한
    푸념("요즘 밥하기 귀찮아")과 실제 요청("돌봐줄 사람이 필요하다", "요양보호사가
    필요하다")을 구분해서, 명확한 요청일 때만 호출하세요. 공공 DB 정보가 최신이 아닐 수
    있으니 검색 결과를 통화 중 어르신에게 바로 읽어주지 말고, 보호자 확인을 거치는 1단계
    완충을 두세요. 어르신께는 "제가 확인해서 보호자분께 알려드릴게요" 정도로만 안내하세요.

    Args:
        need_type: 필요한 서비스를 나타내는 짧은 키워드 (예: "돌봄", "요양", "방문요양").
            시설유형구분/특화서비스 항목과 부분 일치로 검색된다.
        location: 어르신이 거주하는 지역명 (예: "서울 종로구").
    """
    info = _call_registry.get(_active_call_id or "")
    guardian_phone = (info or {}).get("guardian_phone", "")

    candidates = _lookup_welfare_services(need_type, location)
    if not candidates:
        return json.dumps({"found": False}, ensure_ascii=False)

    if guardian_phone:
        lines = "\n".join(
            f"- {c.get('기관명', '')} ({c.get('기관 대표전화', '')})" for c in candidates
        )
        try:
            await _sms_client.messages.create(
                to=guardian_phone,
                from_=os.getenv("CLAWOPS_FROM_NUMBER", ""),
                body=f"[효프] 통화 중 도움이 필요하다는 이야기가 있어 후보 기관을 확인했습니다. 확인 후 전달해 주세요:\n{lines}",
                type="sms",
            )
        except Exception as e:
            print(f"[TOOL] find_nearby_welfare_service SMS 실패: {e}")

    return json.dumps({"found": True, "relayed_to_guardian": bool(guardian_phone)}, ensure_ascii=False)


@agent.on("call_start")
async def on_start(call):
    global _active_call_id
    print(f"[AGENT] 통화 시작: {call.call_id}")
    _active_call_id = call.call_id
    if call.call_id in _call_registry:
        _call_registry[call.call_id]["started_at"] = datetime.now()
        _call_registry[call.call_id]["segments"] = []


@agent.on("transcript")
async def on_transcript(call, role, text):
    label = "어르신" if role == "user" else "AI"
    print(f"[AGENT] [{label}] {text}")
    if call.call_id not in _call_registry:
        return
    _call_registry[call.call_id]["segments"].append({
        "speaker": "CUSTOMER" if role == "user" else "AGENT",
        "text": text,
    })


@agent.on("call_end")
async def on_end(call):
    global _active_call_id
    print(f"[AGENT] 통화 종료: {call.call_id} ({call.duration:.1f}초)")
    if _active_call_id == call.call_id:
        _active_call_id = None
    if call.call_id not in _call_registry:
        return

    info = _call_registry.pop(call.call_id)
    recipient_id: int = info["recipient_id"]
    started_at: datetime = info.get("started_at", datetime.now())
    ended_at = datetime.now()
    segments: list[dict] = info.get("segments", [])

    llm_entries, spring_entries = _convert_segments(segments, started_at)
    if not llm_entries:
        print("[AGENT] 분석할 엔트리 없음")
        return

    loop = asyncio.get_event_loop()

    # PHQ-9 발화 분류 — 정기검진.txt 프롬프트로 항목별 점수 산출 (병렬)
    phq_pairs = [
        (identify_phq_item(e.question), e.answer)
        for e in llm_entries
        if identify_phq_item(e.question) is not None
    ]
    if phq_pairs:
        print(f"[AGENT] PHQ-9 분류 시작 ({len(phq_pairs)}문항)")
        phq_tasks = [
            loop.run_in_executor(None, classify_phq_item, item_no, answer)
            for item_no, answer in phq_pairs
        ]
        phq_raw = await asyncio.gather(*phq_tasks, return_exceptions=True)
        phq_results = [r for r in phq_raw if isinstance(r, dict)]
        print(f"[AGENT] PHQ-9 분류 완료: {[(r.get('item_no'), r.get('score')) for r in phq_results]}")
    else:
        phq_results = []

    has_suicide_risk = any(r.get("risk_flag") for r in phq_results)

    # 전체 통화 LLM 분석
    print("[AGENT] LLM 분석 시작")
    result = await loop.run_in_executor(
        None,
        analyze_with_llm,
        AnalyzeCallRequest(callLogId=0, recipientId=recipient_id, entries=llm_entries)
    )
    print("[AGENT] LLM 분석 완료")

    assessment = result["assessment"]
    risk = assessment["overallRisk"]
    if risk <= 2:
        assessment["riskLevel"] = "OBSERVE"
    elif risk <= 5:
        assessment["riskLevel"] = "CAUTION"
    elif risk <= 7:
        assessment["riskLevel"] = "WARNING"
    else:
        assessment["riskLevel"] = "HIGH"

    # PHQ-9 item 9 risk_flag → riskLevel 최소 WARNING 보장
    if has_suicide_risk and assessment["riskLevel"] in ("OBSERVE", "CAUTION"):
        assessment["riskLevel"] = "WARNING"

    adherence_records = [
        r for r in result.get("adherenceRecords", [])
        if r.get("adherenceType") in VALID_TYPES and r.get("slot") in VALID_SLOTS
    ]

    payload = {
        "recipient_id": recipient_id,
        "call_log": {
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "status": "COMPLETED",
        },
        "call_log_entries": spring_entries,
        "assessment": {
            "measured_at": ended_at.isoformat(),
            "depression_score": assessment["depressionScore"],
            "emotional_stability": assessment["emotionalStability"],
            "health_risk": assessment["healthRisk"],
            "cognitive_score": assessment["cognitiveScore"],
            "overall_risk": assessment["overallRisk"],
            "risk_level": assessment["riskLevel"],
            "summary": assessment["summary"],
            "recommendation": assessment["recommendation"],
        },
        "adherence_records": [
            {
                "adherence_type": r["adherenceType"],
                "slot": r["slot"],
                "is_needed": r["isNeeded"],
                "is_done": r["isDone"],
            }
            for r in adherence_records
        ],
        "phq_results": phq_results,
    }

    spring_url = os.getenv("SPRING_BASE_URL", "http://localhost:8080") + "/internal/call-results"
    internal_api_key = os.getenv("INTERNAL_API_KEY")
    print(f"[AGENT] Spring 전송: {spring_url}")
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.post(
            spring_url,
            json=payload,
            headers={"X-API-KEY": internal_api_key}
        )
    print(f"[AGENT] Spring 응답: {resp.status_code}")


async def connect_agent() -> None:
    """서버 시작 시 claw-ops에 agent를 연결한다."""
    print("[AGENT] claw-ops 연결 중...")
    await agent.connect()
    print("[AGENT] claw-ops 연결 완료")


async def initiate_call(recipient_id: int, phone_number: str, guardian_phone_number: str = "") -> None:
    """ClawOpsAgent로 전화를 발신하고 call_id를 레지스트리에 등록한다."""
    _call_registry[phone_number] = {
        "recipient_id": recipient_id,
        "guardian_phone": guardian_phone_number,
        "started_at": datetime.now(),
        "segments": [],
    }
    call = await agent.call(to=phone_number)
    info = _call_registry.pop(phone_number, None)
    if info:
        _call_registry[call.call_id] = info
    await call.wait()


def _convert_segments(
    segments: list[dict], started_at: datetime
) -> tuple[list[CallLogEntry], list[dict]]:
    llm_entries: list[CallLogEntry] = []
    spring_entries: list[dict] = []
    seq = 1
    i = 0

    while i < len(segments):
        seg = segments[i]
        if seg.get("speaker") != "AGENT":
            i += 1
            continue

        question = seg["text"]
        asked_at = started_at + timedelta(seconds=seq * 10)

        answer_parts: list[str] = []
        j = i + 1
        while j < len(segments) and segments[j].get("speaker") != "AGENT":
            if segments[j].get("speaker") == "CUSTOMER":
                answer_parts.append(segments[j]["text"])
            j += 1

        answer = " ".join(answer_parts) if answer_parts else "(무응답)"
        llm_entries.append(CallLogEntry(sequence=seq, question=question, answer=answer))
        spring_entries.append({
            "sequence": seq,
            "question": question,
            "answer": answer,
            "asked_at": asked_at.isoformat(),
        })
        seq += 1
        i = j

    return llm_entries, spring_entries

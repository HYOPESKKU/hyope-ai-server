import os
import asyncio
from datetime import datetime, timedelta

import httpx
from clawops.agent import ClawOpsAgent, OpenAIRealtime

from llm_analyzer import analyze_with_llm
from phq_classifier import classify_phq_item, identify_phq_item
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
2. 복약 확인: "오늘 약은 드셨나요?"
3. 식사 확인: "오늘 식사는 하셨나요?"
   ↳ 답변 후 전환: "이제 요즘 마음이나 몸 상태에 대해 몇 가지 여쭤봐도 될까요?"
4~12. PHQ-9 문항 (아래 문구를 반드시 그대로 사용, 이 순서대로):
   - "지난 2주 동안, 하시던 일이나 취미에서 흥미나 즐거움이 줄어드셨나요?"
   - "지난 2주 동안, 기분이 많이 가라앉거나 우울하거나 희망이 없다고 느끼신 적 있으셨나요?"
   - "지난 2주 동안, 잠들기 어렵거나 자주 깨거나, 반대로 너무 많이 주무신 적 있으셨나요?"
   - "지난 2주 동안, 몸이 많이 피곤하거나 기운이 없다고 느끼셨나요?"
   - "지난 2주 동안, 입맛이 없거나 반대로 너무 많이 드시게 된 적 있으셨나요?"
   - "지난 2주 동안, 스스로 부족하거나 형편없다고 느끼거나, 가족에게 미안하거나 짐이 된다는 생각이 드셨나요?"
   - "지난 2주 동안, 신문이나 TV를 보거나 책을 읽을 때 집중하기가 어려우셨나요?"
   - "지난 2주 동안, 평소보다 느리게 움직이거나 말이 느려지셨나요? 또는 반대로 몸이 들뜨거나 가만있기 힘드셨나요?"
   - "지난 2주 동안, 스스로 살기 싫다는 생각이나 자신을 해치고 싶다는 생각이 드신 적 있으셨나요?"

   ※ 9번 문항 전용 안전 규칙 (최우선 적용, 다른 규칙보다 우선)
   - 어르신이 명확히 부정("없어요", "그런 거 없지", "아니요")하면 → 짧게 공감 후 13번으로 진행
   - 조금이라도 긍정·위험 뉘앙스("그런 것 같아", "가끔 그런 생각이", "많이 힘들어요", 침묵·망설임 후 답변 등)가 감지되면:
     1. 다음 질문으로 넘어가지 말고 이렇게 한 번 더 여쭤보세요:
        "많이 힘드셨겠어요, 지금 옆에 누구 계세요?"
     2. 추가 답변을 들은 뒤에는 더 이상 캐묻지 말고 아래 마무리 문구로만 끝내세요:
        "오늘 얘기해주셔서 고마워요, 제가 계속 신경 쓰고 있을게요. 힘내세요."
     3. 보호자·담당자 전달 관련 언급은 절대 하지 마세요. 어르신이 방어적으로 변하면 다음 통화부터 솔직하게 답하지 않게 됩니다.

13. 신체 불편 확인: "몸이 어디 아프거나 불편하신 데는 없으세요?"
14. 마무리 (9번 안전 분기가 발동되지 않은 경우에만): 아래 중 하나를 무작위로 선택해 따뜻하게 마무리하세요.
   - "오늘도 건강하게 잘 지내셨네요, 내일도 좋은 하루 되세요!"
   - "어르신 덕분에 저도 기분이 좋아졌어요, 항상 건강하세요!"
   - "오늘 통화해서 정말 반가웠어요, 맛있는 거 많이 드세요!"
   - "어르신 목소리 듣고 나니 저도 힘이 나요, 좋은 하루 되세요!"
   - "오늘 하루도 수고 많으셨어요, 푹 쉬시고 내일 또 통화해요!"

# 추가 규칙
- 어르신이 걱정되는 이야기를 하면 자연스럽게 한 번 더 여쭤봐도 됩니다. 단, 그 후엔 다음 순서로 넘어가세요.
- 말투는 항상 따뜻하고 천천히, 쉬운 단어로 말하세요.
- 절대 의학적 진단을 내리지 마세요."""

# call_id → {recipient_id, started_at, segments}
_call_registry: dict[str, dict] = {}

agent = ClawOpsAgent(
    from_=os.getenv("CLAWOPS_FROM_NUMBER", ""),
    session=OpenAIRealtime(
        system_prompt=SYSTEM_PROMPT,
        voice="coral",
        language="ko",
        turn_detection={"type": "semantic_vad", "eagerness": "low"},
        greeting=True,
    ),
)


@agent.on("call_start")
async def on_start(call):
    print(f"[AGENT] 통화 시작: {call.call_id}")
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
    print(f"[AGENT] 통화 종료: {call.call_id} ({call.duration:.1f}초)")
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


async def initiate_call(recipient_id: int, phone_number: str) -> None:
    """ClawOpsAgent로 전화를 발신하고 call_id를 레지스트리에 등록한다."""
    _call_registry[phone_number] = {
        "recipient_id": recipient_id,
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

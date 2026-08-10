import asyncio
import os
from datetime import datetime

import httpx
from fastapi import APIRouter, BackgroundTasks, Form
from fastapi.responses import Response
from openai import OpenAI

from claw_handler import _call_registry, _convert_segments
from llm_analyzer import analyze_with_llm
from phq_classifier import PHQ_QUESTIONS, classify_phq_item, identify_phq_item
from schemas import AnalyzeCallRequest

router = APIRouter()

VALID_TYPES = {"MEDICATION", "MEAL"}
VALID_SLOTS = {"BREAKFAST", "LUNCH", "DINNER"}


def _xml(body: str) -> Response:
    return Response(
        content=f'<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n{body}\n</Response>',
        media_type="application/xml",
    )


# ── 공감 생성 ──────────────────────────────────────────────────────────────────

_EMPATHY_SYSTEM = """\
당신은 어르신께 전화드리는 효프 AI입니다.
어르신이 방금 하신 말씀에 대해 자연스럽고 따뜻한 공감 한 문장만 생성하세요.
- 진단, 조언, 추가 질문 절대 금지
- 15자 이내, 구어체 한국어
- 예: "많이 힘드셨겠어요.", "다행이네요.", "그러셨군요.", "걱정이 되시겠어요."
- 말이 없었거나 알아듣기 힘들면: "네, 알겠습니다."
JSON 없이 문장만 출력."""


def _generate_empathy(speech: str) -> str:
    try:
        client = OpenAI(
            api_key=os.getenv("GEMINI_API_KEY"),
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
        resp = client.chat.completions.create(
            model="gemini-2.5-flash",
            messages=[
                {"role": "system", "content": _EMPATHY_SYSTEM},
                {"role": "user", "content": speech or "(무응답)"},
            ],
            temperature=0.7,
            max_tokens=30,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return "네, 알겠습니다."


# ── 챗봇 모드 ──────────────────────────────────────────────────────────────────

_PHQ_LINES = "\n".join(f'   - "{q}"' for q in PHQ_QUESTIONS.values())

_CHAT_SYSTEM = f"""\
당신은 효프(Hyope)의 AI 건강 확인 상담사입니다. 어르신께 전화로 안부를 여쭙니다.

# 핵심 규칙
- 반드시 한 번에 질문 하나만 하세요. 어르신이 답하면 공감 한 마디 후 다음 질문으로 넘어가세요.
- 질문을 두 개 이상 한 문장에 묶지 마세요.
- 답변을 들은 후 "아, 그러셨군요." / "다행이네요." / "많이 힘드셨겠어요." 같은 짧은 공감을 먼저 하세요.
- 전화 대화이므로 응답은 2~3문장 이내로 짧게.
- 구어체, 경어 사용 ("~세요", "~어요")
- 진단, 조언, 처방 절대 금지
- 대화가 충분히 이루어졌다고 판단되면 마무리 인사 후 마지막에 [END] 를 붙이세요.
- 최대 8턴 이내에 마무리하세요.

# 진행 순서 (반드시 이 순서대로, 하나씩)
1. 인사: "안녕하세요, 효프 건강 확인 전화입니다. 잘 지내고 계세요?"
2. 복약 확인: "오늘 약은 드셨나요?"
3. 식사 확인: "오늘 식사는 하셨나요?"
   ↳ 답변 후 전환: "이제 요즘 마음이나 몸 상태에 대해 몇 가지 여쭤봐도 될까요?"
4~12. PHQ-9 문항 (아래 문구를 반드시 그대로 사용, 이 순서대로):
{_PHQ_LINES}

   ※ 9번 문항 전용 안전 규칙 (최우선 적용, 다른 규칙보다 우선)
   - 어르신이 명확히 부정("없어요", "그런 거 없지", "아니요")하면 → 짧게 공감 후 13번으로 진행
   - 조금이라도 긍정·위험 뉘앙스("그런 것 같아", "가끔 그런 생각이", "많이 힘들어요",
     침묵·망설임 후 답변 등)가 감지되면:
     1. 다음 질문으로 넘어가지 말고 이렇게 한 번 더 여쭤보세요:
        "많이 힘드셨겠어요, 지금 옆에 누구 계세요?"
     2. 추가 답변 후 아래 마무리 문구로만 끝내세요:
        "오늘 얘기해주셔서 고마워요, 제가 계속 신경 쓰고 있을게요. 힘내세요. [END]"
     3. 보호자·담당자 전달 관련 언급은 절대 하지 마세요.

13. 신체 불편 확인: "몸이 어디 아프거나 불편하신 데는 없으세요?"
14. 마무리 (9번 안전 분기가 발동되지 않은 경우에만): 아래 중 하나를 무작위로 선택 후 [END]를 붙이세요.
   - "오늘도 건강하게 잘 지내셨네요, 내일도 좋은 하루 되세요! [END]"
   - "어르신 덕분에 저도 기분이 좋아졌어요, 항상 건강하세요! [END]"
   - "오늘 통화해서 정말 반가웠어요, 맛있는 거 많이 드세요! [END]"
   - "어르신 목소리 듣고 나니 저도 힘이 나요, 좋은 하루 되세요! [END]"
   - "오늘 하루도 수고 많으셨어요, 푹 쉬시고 내일 또 통화해요! [END]" """

# call_id → 대화 히스토리 [{"role": "assistant"/"user", "content": "..."}]
_chat_history: dict[str, list[dict]] = {}


def _chat_llm(history: list[dict]) -> str:
    try:
        client = OpenAI(
            api_key=os.getenv("GEMINI_API_KEY"),
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
        messages = [{"role": "system", "content": _CHAT_SYSTEM}]
        messages += history if history else [{"role": "user", "content": "통화를 시작해주세요."}]
        resp = client.chat.completions.create(
            model="gemini-2.5-flash",
            messages=messages,
            temperature=0.7,
            max_tokens=150,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[CHAT] LLM 오류: {e}")
        return "죄송합니다, 잠시 문제가 생겼어요. 감사합니다. [END]"


def _chat_gather(base: str) -> str:
    return (
        f'  <Gather input="speech" timeout="8" action="{base}/voiceml/chat/response"'
        f' speechTimeout="2" language="ko-KR">\n'
        f'    <Pause length="1"/>\n'
        f'  </Gather>\n'
    )


async def _run_end_pipeline(call_id: str, history: list[dict]) -> None:
    """통화 종료 시 chat history → PHQ 채점 + LLM 분석 → Spring /call-result 전송."""
    info = _call_registry.pop(call_id, {})
    recipient_id: int = info.get("recipient_id", 0)
    started_at: datetime = info.get("started_at", datetime.now())
    ended_at = datetime.now()

    segments = [
        {"speaker": "AGENT" if m["role"] == "assistant" else "CUSTOMER", "text": m["content"]}
        for m in history
    ]
    llm_entries, spring_entries = _convert_segments(segments, started_at)
    if not llm_entries:
        print(f"[CHAT] {call_id}: 분석할 엔트리 없음")
        return

    loop = asyncio.get_event_loop()

    # PHQ-9 발화 분류 (병렬)
    phq_pairs = [
        (identify_phq_item(e.question), e.answer)
        for e in llm_entries
        if identify_phq_item(e.question) is not None
    ]
    if phq_pairs:
        phq_raw = await asyncio.gather(
            *[loop.run_in_executor(None, classify_phq_item, item_no, answer)
              for item_no, answer in phq_pairs],
            return_exceptions=True,
        )
        phq_results = [r for r in phq_raw if isinstance(r, dict)]
        print(f"[CHAT] PHQ 채점: {[(r.get('item_no'), r.get('score')) for r in phq_results]}")
    else:
        phq_results = []

    has_suicide_risk = any(r.get("risk_flag") for r in phq_results)

    # 전체 LLM 분석
    result = await loop.run_in_executor(
        None,
        analyze_with_llm,
        AnalyzeCallRequest(callLogId=0, recipientId=recipient_id, entries=llm_entries),
    )

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

    spring_url = os.getenv("SPRING_BASE_URL", "http://localhost:8080") + "/call-result"
    print(f"[CHAT] Spring 전송: {spring_url}")
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.post(spring_url, json=payload)
    print(f"[CHAT] Spring 응답: {resp.status_code}")


@router.api_route("/voiceml", methods=["GET", "POST"])
async def voiceml_chat_script(CallId: str = Form(default="")):
    """챗봇 모드 진입점 — LLM이 첫 인사를 생성하고 음성 응답을 기다린다."""
    base = os.getenv("WEBHOOK_BASE_URL", "")
    loop = asyncio.get_event_loop()
    first_msg = await loop.run_in_executor(None, _chat_llm, [])
    _chat_history[CallId] = [{"role": "assistant", "content": first_msg}]
    print(f"[CHAT] 시작 call_id={CallId}: {first_msg}")
    body = f'  <Say language="ko-KR">{first_msg}</Say>\n' + _chat_gather(base)
    return _xml(body)


@router.post("/voiceml/chat/response")
async def voiceml_chat_response(
    background_tasks: BackgroundTasks,
    CallId: str = Form(default=""),
    SpeechResult: str = Form(default=""),
):
    """어르신 발화를 받아 LLM 응답을 생성하고 다음 Gather를 반환한다."""
    base = os.getenv("WEBHOOK_BASE_URL", "")
    history = _chat_history.setdefault(CallId, [])

    user_text = SpeechResult.strip() or "(무응답)"
    history.append({"role": "user", "content": user_text})
    print(f"[CHAT] 어르신: {user_text}")

    if len(history) >= 16:
        reply = "감사합니다. 건강하게 지내세요. [END]"
    else:
        loop = asyncio.get_event_loop()
        reply = await loop.run_in_executor(None, _chat_llm, history)

    history.append({"role": "assistant", "content": reply})
    print(f"[CHAT] AI: {reply}")

    is_end = "[END]" in reply
    reply_clean = reply.replace("[END]", "").strip()

    if is_end:
        finished_history = list(history)
        _chat_history.pop(CallId, None)
        background_tasks.add_task(_run_end_pipeline, CallId, finished_history)
        body = f'  <Say language="ko-KR">{reply_clean}</Say>\n  <Hangup/>'
    else:
        body = f'  <Say language="ko-KR">{reply_clean}</Say>\n' + _chat_gather(base)
    return _xml(body)

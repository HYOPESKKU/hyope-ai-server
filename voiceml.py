import asyncio
import os

from fastapi import APIRouter, Form
from fastapi.responses import Response
from openai import OpenAI

from phq_classifier import PHQ_QUESTIONS

router = APIRouter()

# call_id → {item_no → score(0-3)} — DTMF로 입력된 PHQ-9 점수 임시 보관
_phq_dtmf: dict[str, dict[int, int]] = {}

_DTMF_PROMPT = (
    "전혀 없으시면 1번, 며칠이면 2번, 일주일 이상이면 3번, "
    "거의 매일이면 4번을 눌러주세요. 말씀하셔도 됩니다."
)

# ── 테스트용 스크립트 (PHQ 4문항, 1분 이내) ──────────────────────────────────
# 문항: 2(우울), 4(피로), 7(집중), 9(자살사고) — timeout 6초
_TEST_PHQ_ITEMS = [2, 4, 7, 9]

_TEST_GREETING = """\
  <Say language="ko-KR">안녕하세요. 효프 건강 확인 전화입니다. 잠깐 여쭤볼게요.</Say>
  <Pause length="1"/>
  <Say language="ko-KR">버튼을 누르거나 말씀해 주셔도 됩니다.</Say>
  <Pause length="1"/>
"""

_TEST_CLOSING = """\
  <Say language="ko-KR">감사합니다. 건강하게 지내세요.</Say>
  <Hangup/>"""

# ── 전체 스크립트 (PHQ 9문항 + 인지/건강/복약) ───────────────────────────────
'''
_PRE_PHQ = """\
  <!-- 인사 -->
  <Say language="ko-KR">안녕하세요. 효프 건강 확인 전화입니다. 잠깐 함께 이야기 나눌게요.</Say>
  <Pause length="1"/>

  <!-- 지남력: 시간 -->
  <Say language="ko-KR">첫 번째로, 오늘이 몇 년도인지 말씀해 주실 수 있으세요?</Say>
  <Pause length="6"/>

  <Say language="ko-KR">지금이 몇 월인가요?</Say>
  <Pause length="5"/>

  <Say language="ko-KR">오늘이 며칠이에요?</Say>
  <Pause length="5"/>

  <Say language="ko-KR">오늘 무슨 요일이에요?</Say>
  <Pause length="5"/>

  <!-- 단어기억 ① 즉각 등록 -->
  <Say language="ko-KR">지금부터 단어 세 개를 말씀드릴게요. 잘 기억해 두세요. 사과, 기차, 노란색입니다. 다시 한 번, 사과, 기차, 노란색.</Say>
  <Pause length="3"/>

  <!-- PHQ-9 안내 -->
  <Say language="ko-KR">이제 최근 기분에 대해 여쭤볼게요. 버튼을 누르거나 말씀해 주셔도 됩니다.</Say>
  <Pause length="1"/>
"""

_POST_PHQ = """\
  <!-- 기분·정서 자유발화 -->
  <Say language="ko-KR">요즘 기분은 어떠세요? 좋은 일이 있었거나 힘드셨던 일이 있으시면 편하게 말씀해 주세요.</Say>
  <Pause length="20"/>

  <!-- 건강 자유발화 -->
  <Say language="ko-KR">몸이 어디 아프거나 불편하신 데는 없으세요?</Say>
  <Pause length="15"/>

  <!-- 단어회상 ② 지연 회상 -->
  <Say language="ko-KR">아까 제가 말씀드린 단어 세 개, 기억나세요? 생각나시는 대로 말씀해 주세요.</Say>
  <Pause length="12"/>

  <!-- 복약·식사 확인 -->
  <Say language="ko-KR">오늘 아침 약을 드셨나요?</Say>
  <Pause length="7"/>

  <Say language="ko-KR">오늘 아침 식사는 하셨나요?</Say>
  <Pause length="7"/>

  <!-- 마무리 -->
  <Say language="ko-KR">오늘 통화해 주셔서 감사합니다. 건강하게 지내세요. 내일 또 전화드릴게요.</Say>
  <Hangup/>"""
'''


def get_phq_dtmf_scores(call_id: str) -> dict[int, int]:
    """DTMF로 수집된 PHQ-9 점수를 반환한다. claw_handler에서 사용."""
    return _phq_dtmf.get(call_id, {})


def clear_phq_dtmf(call_id: str) -> None:
    _phq_dtmf.pop(call_id, None)


def _xml(body: str) -> Response:
    return Response(
        content=f'<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n{body}\n</Response>',
        media_type="application/xml",
    )


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


def _phq_gather(item_no: int, timeout: int = 6) -> str:
    base = os.getenv("WEBHOOK_BASE_URL", "")
    q = PHQ_QUESTIONS[item_no]
    return (
        f'  <Gather input="dtmf speech" numDigits="1" action="{base}/voiceml/phq/{item_no}/dtmf"'
        f' timeout="{timeout}" finishOnKey="">\n'
        f'    <Say language="ko-KR">{q} {_DTMF_PROMPT}</Say>\n'
        f'  </Gather>\n'
        f'  <Pause length="1"/>\n'
    )


def _flow_from(start_item: int) -> str:
    """테스트 문항 중 start_item 이후 남은 문항들을 이어 붙인다."""
    remaining = [i for i in _TEST_PHQ_ITEMS if i >= start_item]
    xml = "".join(_phq_gather(i) for i in remaining)
    return xml + _TEST_CLOSING


# @router.api_route("/voiceml", methods=["GET", "POST"])
# def voiceml_script():
#     return _xml(_TEST_GREETING + _flow_from(_TEST_PHQ_ITEMS[0]))


# ── 챗봇 모드 ─────────────────────────────────────────────────────────────────
# call_id → 대화 히스토리 [{"role": "user"/"assistant", "content": "..."}]
_chat_history: dict[str, list[dict]] = {}

_CHAT_SYSTEM = """\
당신은 효프(Hyope)의 AI 건강 확인 상담사입니다. 어르신께 전화로 안부를 여쭙니다.

# 역할
자연스러운 대화를 통해 아래 항목들을 파악하세요:
- 기분/우울감 (PHQ-9 항목 중심)
- 수면 상태
- 식욕/식사 여부
- 피로감/기운
- 복약 여부
- 신체 불편함

# 대화 규칙
- 한 번에 한 가지만 질문하세요
- 답변에 짧은 공감(1문장)을 먼저 하고 다음 질문으로 넘어가세요
- 전화 대화이므로 응답은 2~3문장 이내로 짧게
- 구어체, 경어 사용 ("~세요", "~어요")
- 진단, 조언, 처방 절대 금지
- 대화가 충분히 이루어졌다고 판단되면 마무리 인사 후 마지막에 [END] 를 붙이세요
- 최대 8턴 이내에 마무리하세요

# 시작
첫 발화는 인사 + 첫 질문으로 시작하세요."""


def _chat_llm(history: list[dict]) -> str:
    try:
        client = OpenAI(
            api_key=os.getenv("GEMINI_API_KEY"),
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
        messages = [{"role": "system", "content": _CHAT_SYSTEM}]
        if history:
            messages += history
        else:
            messages.append({"role": "user", "content": "통화를 시작해주세요."})
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


@router.api_route("/voiceml", methods=["GET", "POST"])
async def voiceml_chat_script(CallId: str = Form(default="")):
    """챗봇 모드 진입점 — LLM이 첫 인사를 생성하고 음성 응답을 기다린다."""
    base = os.getenv("WEBHOOK_BASE_URL", "")
    loop = asyncio.get_event_loop()
    first_msg = await loop.run_in_executor(None, _chat_llm, [])
    _chat_history[CallId] = [{"role": "assistant", "content": first_msg}]
    print(f"[CHAT] 시작 call_id={CallId}: {first_msg}")
    body = (
        f'  <Say language="ko-KR">{first_msg}</Say>\n'
        + _chat_gather(base)
    )
    return _xml(body)


@router.post("/voiceml/chat/response")
async def voiceml_chat_response(
    CallId: str = Form(default=""),
    SpeechResult: str = Form(default=""),
):
    """어르신 발화를 받아 LLM 응답을 생성하고 다음 Gather를 반환한다."""
    base = os.getenv("WEBHOOK_BASE_URL", "")
    history = _chat_history.setdefault(CallId, [])

    user_text = SpeechResult.strip() or "(무응답)"
    history.append({"role": "user", "content": user_text})
    print(f"[CHAT] 어르신: {user_text}")

    # 최대 8턴 초과 시 강제 종료
    if len(history) >= 16:
        history.append({"role": "assistant", "content": "감사합니다. 건강하게 지내세요. [END]"})

    loop = asyncio.get_event_loop()
    reply = await loop.run_in_executor(None, _chat_llm, history)
    history.append({"role": "assistant", "content": reply})
    print(f"[CHAT] AI: {reply}")

    is_end = "[END]" in reply
    reply_clean = reply.replace("[END]", "").strip()

    if is_end:
        _chat_history.pop(CallId, None)
        body = f'  <Say language="ko-KR">{reply_clean}</Say>\n  <Hangup/>'
    else:
        body = (
            f'  <Say language="ko-KR">{reply_clean}</Say>\n'
            + _chat_gather(base)
        )
    return _xml(body)


@router.post("/voiceml/phq/{item_no}/dtmf")
async def phq_dtmf_handler(
    item_no: int,
    CallId: str = Form(default=""),
    Digits: str = Form(default=""),
    SpeechResult: str = Form(default=""),
):
    """DTMF/음성 수신 시 점수 저장 → 공감 한 마디 생성 → 다음 PHQ 항목 반환."""
    if CallId and Digits in ("1", "2", "3", "4"):
        _phq_dtmf.setdefault(CallId, {})[item_no] = int(Digits) - 1

    loop = asyncio.get_event_loop()
    empathy = await loop.run_in_executor(None, _generate_empathy, SpeechResult)

    empathy_xml = f'  <Say language="ko-KR">{empathy}</Say>\n  <Pause length="1"/>\n'
    return _xml(empathy_xml + _flow_from(item_no + 1))

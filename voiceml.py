import os

from fastapi import APIRouter, Form
from fastapi.responses import Response

from phq_classifier import PHQ_QUESTIONS

router = APIRouter()

# call_id → {item_no → score(0-3)} — DTMF로 입력된 PHQ-9 점수 임시 보관
_phq_dtmf: dict[str, dict[int, int]] = {}

_DTMF_PROMPT = (
    "전혀 없으시면 1번, 며칠이면 2번, 일주일 이상이면 3번, "
    "거의 매일이면 4번을 눌러주세요. 말씀하셔도 됩니다."
)

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


def _phq_gather(item_no: int) -> str:
    base = os.getenv("WEBHOOK_BASE_URL", "")
    q = PHQ_QUESTIONS[item_no]
    return (
        f'  <Gather input="dtmf" numDigits="1" action="{base}/voiceml/phq/{item_no}/dtmf"'
        f' timeout="8" finishOnKey="">\n'
        f'    <Say language="ko-KR">{q} {_DTMF_PROMPT}</Say>\n'
        f'  </Gather>\n'
        f'  <Pause length="5"/>\n'
    )


def _flow_from(start_item: int) -> str:
    """start_item번 PHQ-9부터 끝까지 + POST_PHQ 섹션을 이어 붙인다."""
    xml = "".join(_phq_gather(i) for i in range(start_item, 10))
    return xml + _POST_PHQ


@router.api_route("/voiceml", methods=["GET", "POST"])
def voiceml_script():
    return _xml(_PRE_PHQ + _flow_from(1))


@router.post("/voiceml/phq/{item_no}/dtmf")
def phq_dtmf_handler(
    item_no: int,
    CallId: str = Form(default=""),
    Digits: str = Form(default=""),
):
    """DTMF 수신 시 점수를 저장하고 다음 PHQ 항목부터 이어가는 XML을 반환한다.

    DTMF 매핑: 1→score 0, 2→score 1, 3→score 2, 4→score 3
    Gather timeout 시에도 이 엔드포인트가 호출되지 않으므로 XML이 다음 Gather로 자연 진행된다.
    """
    if CallId and Digits in ("1", "2", "3", "4"):
        _phq_dtmf.setdefault(CallId, {})[item_no] = int(Digits) - 1

    return _xml(_flow_from(item_no + 1))

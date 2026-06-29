from schemas import CallLogEntry

# 각 세트는 서로 다른 어르신 상황을 시뮬레이션합니다.
# sequence 번호는 세트 내에서 독립적으로 1부터 시작합니다.

DUMMY_ENTRY_SETS: list[list[CallLogEntry]] = [
    # ── 세트 1: 전반적으로 양호, 점심 복약 누락 ──────────────────────────
    [
        CallLogEntry(sequence=1, question="안녕하세요, 어르신! 오늘 하루 어떻게 보내고 계세요?", answer="응, 오늘은 날씨가 좋아서 잠깐 마당에도 나갔다 왔어."),
        CallLogEntry(sequence=2, question="아이고, 좋으셨겠네요. 혹시 점심 드셨어요?", answer="점심은 딸이 가져다준 된장국이랑 밥 먹었지."),
        CallLogEntry(sequence=3, question="잘 드셨네요. 오늘 점심 때 약은 챙겨 드셨나요?", answer="어... 밥 먹고 나서 깜빡했나봐. 약통 보니까 그대로 있더라고."),
        CallLogEntry(sequence=4, question="괜찮아요, 이따 드시면 돼요. 요즘 잠은 잘 주무세요?", answer="잠은 잘 자. 밤에 한 번 깨긴 하는데 그래도 괜찮아."),
        CallLogEntry(sequence=5, question="그렇군요. 요즘 기분은 어떠세요? 특별히 힘드신 건 없으세요?", answer="기분은 그냥 보통이야. 딸이 주말에 온다니까 그게 좀 기다려지네."),
        CallLogEntry(sequence=6, question="오, 좋으시겠어요! 몸은 어디 특별히 불편하신 데 없으세요?", answer="허리가 가끔 뻐근하긴 한데 크게 아프진 않아."),
    ],

    # ── 세트 2: 우울 신호 + 식사 불량 + 복약 미이행 ─────────────────────
    [
        CallLogEntry(sequence=1, question="어르신, 오늘 아침밥은 드셨어요?", answer="아니... 입맛이 없어서 그냥 넘겼어."),
        CallLogEntry(sequence=2, question="아침을 안 드셨군요. 혹시 요즘 입맛이 없으신 게 며칠째 이러세요?", answer="한 사흘? 뭘 먹어도 맛이 없고 억지로 먹는 느낌이야."),
        CallLogEntry(sequence=3, question="그러시군요... 요즘 기분이 많이 가라앉아 계세요?", answer="그냥... 아들이 연락을 안 하니까. 왜 사나 싶기도 하고."),
        CallLogEntry(sequence=4, question="많이 외로우시겠어요. 아침에 드셔야 할 약은 챙기셨어요?", answer="약? 밥도 안 먹었는데 약은 무슨. 그냥 안 먹었어."),
        CallLogEntry(sequence=5, question="많이 힘드시겠어요. 밖에 나가시거나 누구 만나신 게 있으세요?", answer="아니, 며칠째 방에만 있어. 나가기가 싫어."),
        CallLogEntry(sequence=6, question="혹시 몸이 어디 아프거나 불편하신 곳은요?", answer="머리가 멍하고 기운이 없어. 밥을 안 먹어서 그런가."),
        CallLogEntry(sequence=7, question="저녁은 좀 드실 수 있을 것 같으세요?", answer="모르겠어. 그냥 누워 있고 싶어."),
    ],

    # ── 세트 3: 인지 저하 의심 + 복약 혼란 ──────────────────────────────
    [
        CallLogEntry(sequence=1, question="어르신, 오늘 날짜가 며칠인지 혹시 아세요?", answer="오늘이... 음, 화요일인가? 몇 일인지는 잘 모르겠네."),
        CallLogEntry(sequence=2, question="괜찮아요. 오늘 아침에 뭐 드셨는지 기억하세요?", answer="아침? 먹었는지 안 먹었는지... 먹은 것 같기도 하고 아닌 것 같기도 하고."),
        CallLogEntry(sequence=3, question="약은요? 오늘 약 드셨어요?", answer="약을 아까 먹었는데... 또 먹어야 하나? 아까 먹은 거 맞지?"),
        CallLogEntry(sequence=4, question="네, 하루에 한 번만 드시면 돼요. 요즘 자꾸 잊어버리는 게 많으세요?", answer="그게... 냄비를 올려놓고 깜빡해서 태운 적도 있고, 어디다 뒀는지 모르는 것도 많고."),
        CallLogEntry(sequence=5, question="기분은 어떠세요? 불안하시거나 답답하신 건 없으세요?", answer="답답하지. 내가 왜 이러나 싶어. 전에는 안 이랬는데."),
        CallLogEntry(sequence=6, question="점심은 어떻게 하실 거예요?", answer="점심... 아, 냉장고에 뭐가 있었는데. 근데 뭐였지?"),
    ],

    # ── 세트 4: 신체 이상 증상 + 정서 불안 ──────────────────────────────
    [
        CallLogEntry(sequence=1, question="어르신, 오늘 좀 어떠세요? 어디 불편하신 데 있으세요?", answer="가슴이 좀 답답해. 어젯밤부터 그런 것 같아."),
        CallLogEntry(sequence=2, question="가슴이 답답하시군요. 숨 쉬기가 힘드시거나 두근거리지는 않으세요?", answer="두근거리진 않고... 그냥 뭔가 꽉 막힌 느낌? 잠도 잘 못 잤어."),
        CallLogEntry(sequence=3, question="많이 불편하셨겠어요. 오늘 아침약은 드셨어요?", answer="약은 먹었어. 그거라도 챙겨야지 싶어서."),
        CallLogEntry(sequence=4, question="잘 챙기셨네요. 아침밥도 드셨나요?", answer="조금 먹었어. 죽 좀 쑤어 먹었는데 많이는 못 먹겠더라고."),
        CallLogEntry(sequence=5, question="요즘 걱정되시는 게 있으세요? 마음이 많이 무거우신 것 같아서요.", answer="아이고, 몸이 이러니까 걱정이 되지. 혼자 있다가 쓰러지면 어떡하나 싶고."),
        CallLogEntry(sequence=6, question="그런 걱정이 드시는군요. 가족한테 오늘 연락해보실 수 있을까요?", answer="며느리한테 연락하기가 좀 미안해서... 바쁜 것 같던데."),
        CallLogEntry(sequence=7, question="혹시 어지럽거나 손발이 저리신 건 없으세요?", answer="아까 일어나다가 좀 어지러웠어. 금방 괜찮아지긴 했는데."),
    ],

    # ── 세트 5: 전반 양호, 식사/복약 모두 이행 ──────────────────────────
    [
        CallLogEntry(sequence=1, question="어르신, 오늘 기분 어때요?", answer="오늘은 기분 좋아! 아침에 경로당 다녀왔거든."),
        CallLogEntry(sequence=2, question="오, 경로당 다녀오셨군요! 거기서 뭐 하셨어요?", answer="친구들이랑 화투도 치고, 점심도 같이 먹고. 오랜만에 재미있었어."),
        CallLogEntry(sequence=3, question="좋으셨겠어요. 아침 저녁 약은 다 챙겨 드셨어요?", answer="아침 약은 나가기 전에 먹었고, 저녁 약은 이따 먹을 거야."),
        CallLogEntry(sequence=4, question="잘 챙기고 계시네요. 밥도 잘 드시고 계신 거죠?", answer="응, 오늘 세 끼 다 먹었어. 경로당에서 점심도 줘서 맛있게 먹었지."),
        CallLogEntry(sequence=5, question="혹시 요즘 몸이 어디 불편하신 건 없으세요?", answer="무릎이 가끔 시린데, 그 정도야 뭐. 나이 들면 다 그렇지."),
        CallLogEntry(sequence=6, question="마음은요? 요즘 외롭거나 힘드신 건 없으세요?", answer="친구들이랑 자주 만나니까 괜찮아. 손자도 이번 주말에 온다고 해서 기대돼."),
    ],
]

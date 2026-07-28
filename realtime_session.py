"""OpenAI Realtime API에 직접 붙는 커스텀 Session 구현.

clawops의 ClawOpsAgent(전화 발신/미디어 스트림/통화 제어)는 그대로 쓰되,
clawops가 기본 제공하는 OpenAIRealtime 세션 클래스는 쓰지 않는다 — 그 클래스는
(1) 응답 하나에 tool 호출이 여러 개면 response.create()를 호출마다 따로 불러서
같은 턴에 응답이 중복 생성되고, (2) 통화 응답 전(prewarm) 구간에 인사말을 강제로
미리 만들게 해서 tool 호출 신뢰성이 떨어지는 문제가 실통화에서 반복 관찰됐다.

이 파일은 clawops가 요구하는 Session Protocol(clawops.agent.pipeline._base.Session)만
구현한다: start/prewarm/attach/feed_audio/feed_dtmf/stop/get_telemetry.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI


@dataclass
class _PlaybackState:
    """현재 재생 중인 assistant 응답의 오디오 상태."""

    item_id: str
    audio_remainder: bytes = b""


class RealtimeSession:
    """OpenAI Realtime API 기반 음성 세션. clawops Session Protocol 구현체."""

    def __init__(
        self,
        *,
        system_prompt: str,
        voice: str = "coral",
        language: str = "ko",
        model: str = "gpt-realtime-2",
        api_key: str | None = None,
        turn_detection: dict[str, Any] | None = None,
    ) -> None:
        self.system_prompt = system_prompt
        self.voice = voice
        self.language = language
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        # create_response는 끄고, 유저 턴이 끝났다는 신호(input_audio_buffer.committed)를
        # 우리가 직접 받아서 response.create()를 부른다. 서버가 자동으로 만들면 tool
        # 호출로 트리거하는 response.create()와 서로 다른 경로라 우리 쪽 "턴당 한 번"
        # 가드(_maybe_create_response)가 못 미치고, 한 유저 발화에 응답이 여러 번(같은
        # 말 그대로 반복) 생성되는 문제가 실제로 있었다. 응답 생성 경로를 하나로 모아야
        # 이 가드가 전부 커버한다.
        self.turn_detection = turn_detection or {
            "type": "semantic_vad",
            "eagerness": "low",
            "create_response": False,
            "interrupt_response": True,
        }

        self._tools: Any | None = None  # ClawOpsAgent가 set_tool_registry로 주입
        self._client: AsyncOpenAI | None = None
        self._connection: Any | None = None
        self._call: Any | None = None  # attach() 이전엔 None — 그 전엔 오디오/이벤트가 발생하지 않는다
        self._receive_task: asyncio.Task | None = None
        self._pending_tasks: set[asyncio.Task] = set()
        self._playback: _PlaybackState | None = None
        self._latest_media_ts: int = 0
        # 한 턴(하나의 model response)에 tool 호출이 여러 번 있어도 response.create()는
        # 정확히 한 번만 나가야 한다 — 이미 응답이 진행 중이면 추가 요청을 막고,
        # response.done에서 다시 열어준다.
        self._response_pending: bool = False
        # session.update()는 보내는 것과 서버가 실제로 반영을 마치는 것(session.updated)이
        # 다르다 — 첫 통화(콜드 커넥션)에서 이 확인을 기다리지 않고 attach() 이후 바로
        # 오디오가 들어오면, tool/instructions가 아직 안 실린 세션이 응답을 만들어버려서
        # tool 호출이 안 되고 임의로 말을 지어내는 문제가 실제로 관찰됐다. prewarm()이
        # session.updated를 확인하고 나서야 끝나도록 게이트를 둔다.
        self._session_ready = asyncio.Event()

    # ── clawops Session Protocol ────────────────────────────────

    def set_tool_registry(self, registry: Any) -> None:
        self._tools = registry

    async def prewarm(self) -> None:
        """LLM WS 연결 + session.update만 수행한다. call 없이도 호출 가능.

        인사말은 강제로 생성하지 않는다 — attach() 이전엔 오디오를 넣지 않으므로
        모델이 스스로 말을 시작할 일이 없고, 어르신이 먼저 말을 걸면 그때 응답한다.

        initiate_call()이 agent.call()(발신) 전에 이미 이걸 완전히
        끝내놓고, ClawOpsAgent(prewarm_enabled=False)라서 clawops 자체의 자동
        prewarm은 안 걸린다. 그래도 혹시 start()를 통해 다시 불리면(예: 재시도)
        이미 연결돼 있으면 그대로 두고 중복 연결하지 않는다."""
        if self._connection is not None:
            return
        self._client = AsyncOpenAI(api_key=self.api_key)
        manager = self._client.realtime.connect(model=self.model)
        self._connection = await manager.enter()

        tool_schemas = self._tools.to_openai_tools() if self._tools else []
        await self._connection.session.update(
            session={
                "type": "realtime",
                "output_modalities": ["audio"],
                "instructions": self.system_prompt,
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcmu"},
                        "noise_reduction": {"type": "near_field"},
                        "transcription": {
                            "model": "gpt-4o-transcribe",
                            "language": self.language,
                        },
                        "turn_detection": self.turn_detection,
                    },
                    "output": {
                        "format": {"type": "audio/pcmu"},
                        "voice": self.voice,
                    },
                },
                "tools": tool_schemas,
                "tracing": "auto",
            }
        )
        self._receive_task = asyncio.create_task(self._receive_loop())

        try:
            await asyncio.wait_for(self._session_ready.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            print("[AGENT] session.updated 확인 타임아웃 — 세션 설정 없이 진행")

    async def attach(self, call: Any) -> None:
        """실제 통화가 연결된 뒤에만 불린다 — prewarm 단계(BufferingCall)와 달리
        여기선 call이 진짜이므로, 첫 인사를 여기서 트리거해도 예전 prewarm 강제
        인사말 때 겪었던 문제(응답 전 tool 오작동 등)가 재현되지 않는다."""
        self._call = call
        await self._maybe_create_response(reason="greeting")

    async def start(self, call: Any) -> None:
        await self.prewarm()
        await self.attach(call)

    async def feed_audio(self, audio: bytes, timestamp: int) -> None:
        self._latest_media_ts = timestamp
        if self._connection:
            await self._connection.input_audio_buffer.append(
                audio=base64.b64encode(audio).decode(),
            )

    async def feed_dtmf(self, digits: str) -> None:
        # DTMF는 이 서비스와 무관해서 지원하지 않는다.
        return

    async def stop(self) -> None:
        if self._connection:
            try:
                await asyncio.wait_for(self._connection.close(), timeout=2.0)
            except Exception as e:
                print(f"[AGENT] Realtime 연결 종료 중 에러: {e}")
            self._connection = None

        current = asyncio.current_task()
        pending = [
            t for t in ({self._receive_task} | self._pending_tasks)
            if t is not None and t is not current
        ]
        for task in pending:
            if not task.done():
                task.cancel()
        self._pending_tasks.clear()
        self._receive_task = None
        self._call = None
        self._playback = None
        self._response_pending = False
        self._session_ready = asyncio.Event()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def get_telemetry(self) -> dict[str, Any] | None:
        return {
            "sessionType": "custom_openai_realtime",
            "model": self.model,
            "voice": self.voice,
            "language": self.language,
        }

    # ── 내부 이벤트 처리 ─────────────────────────────────────────

    async def _receive_loop(self) -> None:
        if not self._connection:
            return
        try:
            async for event in self._connection:
                await self._handle_event(event)
        except Exception as e:
            print(f"[AGENT] Realtime 수신 루프 에러: {e}")

    async def _handle_event(self, event: Any) -> None:
        event_type = event.type

        if event_type == "session.updated":
            self._session_ready.set()
        elif event_type == "response.output_audio.delta":
            await self._handle_audio_delta(event)
        elif event_type == "response.output_audio.done":
            await self._flush_playback_remainder()
        elif event_type == "response.output_item.done":
            item = event.item
            if item and item.type == "function_call":
                task = asyncio.create_task(self._handle_function_call(item))
                self._pending_tasks.add(task)
                task.add_done_callback(self._pending_tasks.discard)
        elif event_type == "response.done":
            print(f"[AGENT] response.done (status={getattr(event.response, 'status', '?')})")
            self._response_pending = False
        elif event_type == "input_audio_buffer.speech_started":
            await self._handle_interrupt()
        elif event_type == "input_audio_buffer.committed":
            # 유저 턴이 확정된 시점 — create_response를 꺼뒀으니 여기서 직접
            # 응답 생성을 트리거한다. tool 호출 후 트리거와 같은 가드를 타므로
            # 이미 응답이 진행 중이면 중복 생성되지 않는다.
            await self._maybe_create_response(reason="user_committed")
        elif event_type == "conversation.item.input_audio_transcription.completed":
            if self._call:
                await self._call._emit("transcript", "user", event.transcript or "")
        elif event_type == "response.output_audio_transcript.done":
            if self._call:
                await self._call._emit("transcript", "assistant", event.transcript or "")
        elif event_type == "error":
            print(f"[AGENT] OpenAI Realtime 에러: {event.error}")

    async def _handle_audio_delta(self, event: Any) -> None:
        if not self._call:
            return
        if self._playback is None:
            self._playback = _PlaybackState(item_id=event.item_id or "")
        pb = self._playback
        ulaw = pb.audio_remainder + base64.b64decode(event.delta)
        chunk_size = 160  # G.711 ulaw, 20ms 프레임
        full_end = (len(ulaw) // chunk_size) * chunk_size
        for off in range(0, full_end, chunk_size):
            await self._call.send_audio(ulaw[off:off + chunk_size])
        pb.audio_remainder = ulaw[full_end:]

    async def _flush_playback_remainder(self) -> None:
        if not self._playback or not self._call:
            self._playback = None
            return
        if self._playback.audio_remainder:
            padded = self._playback.audio_remainder + b"\xff" * (160 - len(self._playback.audio_remainder))
            await self._call.send_audio(padded)
        self._playback = None

    async def _handle_interrupt(self) -> None:
        for task in list(self._pending_tasks):
            if not task.done():
                task.cancel()
        self._pending_tasks.clear()
        if self._call:
            await self._call.clear_audio()
        self._playback = None

    async def _handle_function_call(self, item: Any) -> None:
        func_name = item.name or ""
        call_id = item.call_id or ""
        args = json.loads(item.arguments or "{}")
        print(f"[AGENT] Tool call: {func_name}({args})")

        if self._tools is None or func_name not in self._tools:
            result = json.dumps({"error": f"Unknown tool: {func_name}"})
        else:
            try:
                result = await self._tools.call(func_name, args)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                result = f"Error: {e}"

        if self._connection:
            await self._connection.conversation.item.create(
                item={
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": str(result),
                }
            )
        await self._maybe_create_response(reason="tool_output")

    async def _maybe_create_response(self, reason: str = "") -> None:
        """tool 실행이 끝나 후속 응답을 만들려는 시점에, 그 tool 호출을 낳은 응답
        자체의 response.done이 아직 처리되기 전이면 _response_pending이 True로
        남아있어 여기서 영영 억제될 수 있다(실제로 관찰된, 통화가 조용히 멈추는
        원인으로 의심됨). function_call만 있는 응답은 보통 곧바로 끝나므로,
        바로 포기하지 않고 짧게 재시도해서 그 경쟁 창을 흡수한다."""
        for attempt in range(20):
            if not self._connection:
                return
            if not self._response_pending:
                self._response_pending = True
                print(f"[AGENT] response.create() 호출 (reason={reason}, attempt={attempt})")
                await self._connection.response.create()
                return
            await asyncio.sleep(0.1)
        print(f"[AGENT] response.create() 포기 (reason={reason}, 계속 억제 상태)")

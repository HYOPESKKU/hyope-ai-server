
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

log = logging.getLogger("hyope.realtime")

REALTIME_URL = "wss://api.openai.com/v1/realtime"

_FEED_READY_TIMEOUT_S = 3.0        # 재연결 중 들어온 프레임을 무한정 막지 않기 위한 상한
_HANDSHAKE_TIMEOUT_S = 10.0
_WATCHDOG_INTERVAL_S = 2.0
_WATCHDOG_WARN_S = 8.0             # 무이벤트 경고 임계값
_WATCHDOG_RECONNECT_S = 15.0       # 무이벤트 재연결 임계값
_RECONNECT_MAX_ATTEMPTS = 3
_RECONNECT_BASE_DELAY_S = 1.0



_HANG_UP_TOOL = {
    "type": "function",
    "name": "hang_up",
    "description": "통화를 종료합니다. 대화가 자연스럽게 끝났거나 어르신이 인사하고 끊으려 할 때 호출하세요.",
    "parameters": {"type": "object", "properties": {}},
}


@dataclass
class _Playback:
    item_id: str
    generating: bool = True
    remainder: bytes = b""


class CourseRealtimeSession:
    """OpenAI Realtime API를 직접 WebSocket으로 붙이는 네이티브 음성 세션."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        system_prompt: str = "",
        model: str = "gpt-realtime-2",
        voice: str = "marin",
        language: str = "ko",
        turn_detection: dict[str, Any] | None = None,
        greeting: bool = True,
    ) -> None:
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not self._api_key:
            raise ValueError("OPENAI_API_KEY가 필요합니다.")
        self._system_prompt = system_prompt
        self._model = model
        self._voice = voice
        self._language = language
        self._turn_detection = turn_detection or {"type": "semantic_vad", "eagerness": "low"}
        self._greeting = greeting

        self._tools: Any = None  # ToolRegistry — set_tool_registry로 clawops가 주입
        self._ws: Any = None
        self._call: Any = None
        self._ready = asyncio.Event()
        self._recv_task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._playback: _Playback | None = None

        # double-reply bug 방지용 상태 (week5 slide6). tool call이 하나도 없던 turn은
        # 절대 followup 대상이 아니므로 _tool_call_responses에 있는지로 먼저 걸러낸다.
        self._tool_call_responses: set[str] = set()      # tool call이 한 번이라도 있었던 response_id
        self._pending_calls: dict[str, set[str]] = {}    # response_id -> 아직 결과 안 낸 call_id들
        self._response_finished: set[str] = set()         # response.done 도착한 response_id
        self._pending_tool_tasks: set[asyncio.Task] = set()

        self._recent_transcript: list[dict[str, str]] = []  # 재연결 시 컨텍스트 복원용
        self._last_event_ts = time.monotonic()
        self._stopped = False
        self._reconnect_attempts = 0
        self._reconnecting = False  # recv_loop 종료와 watchdog이 동시에 재연결을 트리거하지 않도록

    def set_tool_registry(self, registry: Any) -> None:
        """clawops는 전화가 링 중일 때 prewarm()으로 먼저 연결한 뒤, 통화가 잡히고
        나서야 이 메서드로 tool registry를 넘겨준다 — 즉 session.update가 이미
        나간 뒤에 tool 목록이 도착하는 경우가 보통이다. 이미 연결돼 있으면
        session.update를 한 번 더 보내 tool 목록을 반영한다(OpenAI Realtime API의
        session.update는 부분 갱신이라 나머지 설정은 그대로 유지된다)."""
        self._tools = registry
        if self._ws is not None:
            asyncio.create_task(self._resend_tools())

    async def _resend_tools(self) -> None:
        await self._ready.wait()
        tools = [_HANG_UP_TOOL]
        if self._tools is not None:
            tools.extend(self._tools.to_openai_tools())
        await self._send({"type": "session.update", "session": {"type": "realtime", "tools": tools}})

    def get_telemetry(self) -> dict[str, Any]:
        return {
            "sessionType": "course_realtime_native",
            "llm": {"provider": "openai", "model": self._model},
            "voice": self._voice,
            "language": self._language,
            "greetingEnabled": self._greeting,
        }

    # ── Session Protocol ─────────────────────────────────────────

    async def prewarm(self) -> None:
        await self._connect()

    async def attach(self, call: Any) -> None:
        self._call = call
        await self._maybe_greet()

    async def start(self, call: Any) -> None:
        self._call = call
        await self._connect()
        await self._maybe_greet()

    async def feed_audio(self, audio: bytes, timestamp: int) -> None:
        if self._stopped:
            return
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=_FEED_READY_TIMEOUT_S)
        except asyncio.TimeoutError:
            return  # 세션이 준비 안 됨 — 이 프레임은 버린다, watchdog이 복구를 책임진다
        await self._send({
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(audio).decode(),
        })

    async def feed_dtmf(self, digits: str) -> None:
        await self._send({
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": f"[DTMF 입력: {digits}]"}],
            },
        })
        await self._send({"type": "response.create"})

    async def stop(self) -> None:
        self._stopped = True
        tasks = [self._recv_task, self._watchdog_task, *self._pending_tool_tasks]
        current = asyncio.current_task()
        for task in tasks:
            if task and task is not current and not task.done():
                task.cancel()
        pending = [t for t in tasks if t and t is not current]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._pending_tool_tasks.clear()
        await self._close_ws()

    # ── 연결 / 핸드셰이크 ─────────────────────────────────────────

    async def _connect(self) -> None:
        url = f"{REALTIME_URL}?model={self._model}"
        self._ws = await ws_connect(
            url,
            additional_headers={"Authorization": f"Bearer {self._api_key}"},
        )
        self._ready.clear()
        self._last_event_ts = time.monotonic()
        self._recv_task = asyncio.create_task(self._recv_loop())
        if self._watchdog_task is None:
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())

        tools = [_HANG_UP_TOOL]
        if self._tools is not None:
            tools.extend(self._tools.to_openai_tools())

        await self._send({
            "type": "session.update",
            "session": {
                "type": "realtime",
                "output_modalities": ["audio"],
                "instructions": self._system_prompt,
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcmu"},
                        "noise_reduction": {"type": "near_field"},
                        "transcription": {"model": "gpt-4o-transcribe", "language": self._language},
                        "turn_detection": self._turn_detection,
                    },
                    "output": {
                        "format": {"type": "audio/pcmu"},
                        "voice": self._voice,
                    },
                },
                "tools": tools,
                "tracing": "auto",
            },
        })

        # session.updated 도착 전까지는 오디오를 보내지 않는다 (핸드셰이크 순서, slide 3)
        await asyncio.wait_for(self._ready.wait(), timeout=_HANDSHAKE_TIMEOUT_S)

        if self._recent_transcript:
            await self._replay_context()

    async def _maybe_greet(self) -> None:
        if self._greeting:
            await self._send({"type": "response.create"})

    async def _replay_context(self) -> None:
        """재연결 직후 최근 대화를 다시 넣어준다 — 네이티브 세션은 재연결하면 기억을 잃으므로
        캐스케이드(week4)의 "그냥 히스토리를 다시 보낸다" 방식을 빌려온다 (week5 slide10)."""
        for turn in self._recent_transcript[-6:]:
            role = "assistant" if turn["speaker"] == "AGENT" else "user"
            content_type = "input_text" if role == "user" else "text"
            await self._send({
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": role,
                    "content": [{"type": content_type, "text": turn["text"]}],
                },
            })

    async def _send(self, payload: dict[str, Any]) -> None:
        if not self._ws:
            return
        try:
            await self._ws.send(json.dumps(payload))
        except ConnectionClosed:
            pass

    async def _close_ws(self) -> None:
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    # ── 수신 루프 ─────────────────────────────────────────────────

    async def _recv_loop(self) -> None:
        try:
            async for raw in self._ws:
                self._last_event_ts = time.monotonic()
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self._handle_event(event)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning(f"[Realtime] 수신 루프 종료: {e}")
        finally:
            if not self._stopped:
                asyncio.create_task(self._handle_disconnect())

    async def _handle_event(self, event: dict[str, Any]) -> None:
        etype = event.get("type")

        if etype == "session.updated":
            self._ready.set()
            self._reconnect_attempts = 0

        elif etype == "response.output_audio.delta":
            await self._on_audio_delta(event)

        elif etype == "response.output_audio.done":
            await self._on_audio_done()

        elif etype == "input_audio_buffer.speech_started":
            await self._on_barge_in()

        elif etype == "response.output_item.done":
            item = event.get("item") or {}
            if item.get("type") == "function_call":
                resp_id = event.get("response_id", "")
                self._tool_call_responses.add(resp_id)
                self._pending_calls.setdefault(resp_id, set()).add(item.get("call_id", ""))
                task = asyncio.create_task(self._run_tool_call(resp_id, item))
                self._pending_tool_tasks.add(task)
                task.add_done_callback(self._pending_tool_tasks.discard)

        elif etype == "response.done":
            resp_id = (event.get("response") or {}).get("id", "")
            self._response_finished.add(resp_id)
            await self._maybe_followup(resp_id)

        elif etype == "conversation.item.input_audio_transcription.completed":
            await self._emit_transcript("user", event.get("transcript") or "")

        elif etype == "response.output_audio_transcript.done":
            await self._emit_transcript("assistant", event.get("transcript") or "")

        elif etype == "error":
            log.error(f"[Realtime] error: {event.get('error')}")

    async def _emit_transcript(self, role: str, text: str) -> None:
        self._recent_transcript.append({
            "speaker": "AGENT" if role == "assistant" else "CUSTOMER",
            "text": text,
        })
        if self._call:
            await self._call._emit("transcript", role, text)

    async def _on_audio_delta(self, event: dict[str, Any]) -> None:
        if self._playback is None:
            self._playback = _Playback(item_id=event.get("item_id") or "")
        pb = self._playback
        ulaw = pb.remainder + base64.b64decode(event["delta"])
        chunk = 160  # 20ms @ 8kHz µ-law (week2 slide5: 프레임은 20ms 단위)
        end = (len(ulaw) // chunk) * chunk
        for off in range(0, end, chunk):
            if self._call:
                await self._call.send_audio(ulaw[off:off + chunk])
        pb.remainder = ulaw[end:]

    async def _on_audio_done(self) -> None:
        if self._playback:
            self._playback.generating = False
            if self._playback.remainder and self._call:
                padded = self._playback.remainder + b"\xff" * (160 - len(self._playback.remainder))
                await self._call.send_audio(padded)
        self._playback = None

    async def _on_barge_in(self) -> None:
        """감지는 서버가 했다 — 재생 중단과 진행 중이던 응답/tool 취소는 우리 몫 (week5 slide5/8)."""
        for task in list(self._pending_tool_tasks):
            task.cancel()
        try:
            await self._send({"type": "response.cancel"})
        except Exception:
            pass
        if self._call:
            await self._call.clear_audio()
        self._playback = None

    # ── 함수 호출 (tool calling) ─────────────────────────────────

    async def _run_tool_call(self, resp_id: str, item: dict[str, Any]) -> None:
        name = item.get("name") or ""
        call_id = item.get("call_id") or ""

        if name == "hang_up":
            self._pending_calls.get(resp_id, set()).discard(call_id)
            if self._call:
                await self._call.hangup()
            return

        try:
            args = json.loads(item.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}

        try:
            if self._tools is not None and name in self._tools:
                result = await self._tools.call(name, args)
            else:
                result = json.dumps({"error": f"Unknown tool: {name}"})
        except asyncio.CancelledError:
            self._pending_calls.get(resp_id, set()).discard(call_id)
            raise
        except Exception as e:
            result = json.dumps({"error": str(e)})

        await self._send({
            "type": "conversation.item.create",
            "item": {"type": "function_call_output", "call_id": call_id, "output": str(result)},
        })
        self._pending_calls.get(resp_id, set()).discard(call_id)
        await self._maybe_followup(resp_id)

    async def _maybe_followup(self, resp_id: str) -> None:
        """이 turn에 tool call이 있었고, 모든 결과가 제출되고, response.done도 왔을 때만
        정확히 한 번 response.create를 보낸다. tool call이 없던 평범한 turn까지 여기서
        response.create를 다시 보내면 모델이 매 턴 두 번씩 대답하게 된다 — 반드시
        _tool_call_responses로 "이 turn에 tool call이 있었는지"부터 걸러야 한다
        (week5 slide6, "double-reply bug")."""
        if resp_id not in self._tool_call_responses:
            return
        if resp_id not in self._response_finished:
            return
        if self._pending_calls.get(resp_id):
            return
        self._tool_call_responses.discard(resp_id)
        self._response_finished.discard(resp_id)
        self._pending_calls.pop(resp_id, None)
        await self._send({"type": "response.create"})

    # ── 워치독 / 재연결 (week5 slide9~10) ──────────────────────────

    async def _watchdog_loop(self) -> None:
        try:
            while not self._stopped:
                await asyncio.sleep(_WATCHDOG_INTERVAL_S)
                if self._stopped:
                    return
                silence = time.monotonic() - self._last_event_ts
                # 에이전트가 재생 중이면 무이벤트가 정상 — 오탐 방지 (slide9의 핵심 포인트)
                if self._playback is not None and self._playback.generating:
                    continue
                if silence > _WATCHDOG_RECONNECT_S:
                    log.warning(f"[Watchdog] {silence:.1f}s 무응답 — 재연결 시도")
                    await self._handle_disconnect()
                elif silence > _WATCHDOG_WARN_S:
                    log.warning(f"[Watchdog] {silence:.1f}s 무응답 — 경고 단계")
        except asyncio.CancelledError:
            pass

    async def _handle_disconnect(self) -> None:
        """recv_loop 종료(자연 끊김)와 watchdog(무응답 감지)이 둘 다 이 메서드를 부를 수 있다
        — _reconnecting으로 동시 진입을 막아 WebSocket이 중복으로 열리지 않게 한다."""
        if self._stopped or self._reconnecting:
            return
        self._reconnecting = True
        try:
            while not self._stopped:
                await self._close_ws()
                self._reconnect_attempts += 1
                if self._reconnect_attempts > _RECONNECT_MAX_ATTEMPTS:
                    log.error("[Realtime] 재연결 한도 초과 — 통화를 정중히 종료합니다.")
                    if self._call:
                        await self._call.hangup()
                    return
                delay = _RECONNECT_BASE_DELAY_S * (2 ** (self._reconnect_attempts - 1))
                await asyncio.sleep(delay)
                try:
                    # _connect()가 _replay_context()로 문맥을 복원한다 — 인사말은 다시 하지 않는다
                    await self._connect()
                    log.info(f"[Realtime] 재연결 성공 (attempt {self._reconnect_attempts})")
                    return
                except Exception as e:
                    log.error(f"[Realtime] 재연결 실패: {e}")
        finally:
            self._reconnecting = False

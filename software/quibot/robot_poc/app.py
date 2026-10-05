from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from openai import AsyncOpenAI
from openai.resources.realtime.realtime import AsyncRealtimeConnection
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK

from .audio import AudioIO, list_audio_devices
from .config import ConfigurationError, Settings
from .conversation_language import (
    active_language_instruction,
    sleep_farewell_text,
    transcription_retry_text,
)
from .dual_eye import SUPPORTED_EXPRESSIONS, DualEyeClient, FaceCommand
from .logging_utils import SessionLogWriter, configure_logging
from .motion import (
    SUPPORTED_DIRECTIONS,
    SUPPORTED_GESTURE_INPUTS,
    SUPPORTED_GESTURES,
    GestureCommand,
    MotionClient,
)
from .orchestrator import ActionBundle, FaceAction, MotionAction, RobotOrchestrator, SessionAction, SpeechAction
from .sleep_word import match_sleep_word
from .tools import (
    NON_CRITICAL_DUAL_EYE_FAILURE_PREFIXES,
    NON_CRITICAL_FACE_FAILURE_MESSAGE,
    ToolInputError,
    ToolRegistry,
    build_default_tool_registry,
)
from .wake_word import VoskWakeWordListener, WakeWordError, normalize_text


MOTION_TOOL_NAMES = {
    "robot_walk",
    "robot_greet",
    "robot_perform_gesture",
    "robot_move_joint",
    "robot_stop",
}
SLEEP_FAREWELL_PLAYBACK_TIMEOUT_S = 5.0
SLEEP_FAREWELL_METADATA = {"robot_poc_kind": "sleep_farewell"}
FIXED_PHRASE_MAX_OUTPUT_TOKENS = 120
LOW_CONFIDENCE_AVG_LOGPROB = -0.8
EXPECTED_REALTIME_CLOSE_REASONS = {"idle_timeout", "sleep_word"}


def _is_expected_realtime_close(exc: ConnectionClosed) -> bool:
    if isinstance(exc, ConnectionClosedOK):
        return True

    sent = exc.sent
    return (
        sent is not None
        and sent.code == 1000
        and sent.reason in EXPECTED_REALTIME_CLOSE_REASONS
    )

@dataclass
class ResponseState:
    pending_tools: int = 0
    outputs_sent: int = 0
    assistant_output_sent: bool = False
    response_done: bool = False
    continuation_requested: bool = False


class RobotVoiceApp:
    def __init__(
        self,
        settings: Settings,
        tools: ToolRegistry,
        dual_eye: Optional[DualEyeClient] = None,
        motion: Optional[MotionClient] = None,
        *,
        close_dual_eye_on_exit: bool = True,
        close_motion_on_exit: bool = True,
        initial_response_text: Optional[str] = None,
        wake_greeting_enabled: bool = False,
        wake_greeting_already_sent: bool = False,
        orchestrator: Optional[RobotOrchestrator] = None,
        initial_action_bundle: Optional[ActionBundle] = None,
    ) -> None:
        self.settings = settings
        self.tools = tools
        self.dual_eye = dual_eye
        self.motion = motion
        self.close_dual_eye_on_exit = close_dual_eye_on_exit
        self.close_motion_on_exit = close_motion_on_exit
        self.initial_response_text = initial_response_text
        self.initial_action_bundle = initial_action_bundle
        self.wake_greeting_enabled = wake_greeting_enabled
        self.wake_greeting_already_sent = wake_greeting_already_sent
        self.logger = logging.getLogger(__name__)
        self.session_log = SessionLogWriter(settings.log_dir)
        self.audio = AudioIO(
            input_device=settings.input_device,
            output_device=settings.output_device,
            output_gain=settings.output_gain,
            logger=logging.getLogger("robot_poc.audio"),
        )
        self._response_states: Dict[str, ResponseState] = {}
        self._response_total_tokens = 0
        self._transcription_total_tokens = 0
        self._last_activity_at = time.monotonic()
        self._last_face_at = 0.0
        self._last_face_source = ""
        self._last_motion_at = 0.0
        self._last_motion_source = ""
        self._last_motion_gesture = ""
        self._orchestrator = orchestrator or RobotOrchestrator(
            auto_face_events=settings.auto_face_events,
            auto_motion_events=settings.auto_motion_events,
            face_enabled=dual_eye is not None,
            motion_enabled=motion is not None,
        )
        self._sleep_farewell_pending = False
        self._sleep_farewell_response_id: Optional[str] = None
        self._sleep_closing = False
        self._sleep_close_requested = False
        self._response_language = settings.language
        self._client = AsyncOpenAI(api_key=settings.api_key)

    @property
    def orchestrator(self) -> RobotOrchestrator:
        return self._orchestrator

    async def run(self) -> None:
        self.logger.info(
            "Starting Quibot interaction service with model=%s voice=%s language=%s transcription_language=%s barge_in=%s",
            self.settings.model,
            self.settings.voice,
            self.settings.language,
            self.settings.transcription_language or "auto",
            self.settings.allow_barge_in,
        )

        try:
            async with self._client.realtime.connect(model=self.settings.model) as connection:
                await connection.session.update(session=self._build_session_config())
                self.audio.start()
                await asyncio.to_thread(self._warm_up_motion)
                self._prepare_wake_greeting_motion()
                sender = asyncio.create_task(self._send_microphone_audio(connection))
                idle_watcher = self._build_idle_watcher(connection)
                if self.initial_response_text is not None:
                    await self._send_initial_response(connection)
                try:
                    await self._handle_events(connection)
                finally:
                    sender.cancel()
                    if idle_watcher is not None:
                        idle_watcher.cancel()
                    pending_tasks = [sender]
                    if idle_watcher is not None:
                        pending_tasks.append(idle_watcher)
                    await asyncio.gather(*pending_tasks, return_exceptions=True)
                    self.audio.stop()
        finally:
            if self.dual_eye is not None and self.close_dual_eye_on_exit:
                self.dual_eye.close()
            if self.motion is not None and self.close_motion_on_exit:
                self.motion.close()
            await self._client.close()

    def _build_session_config(self) -> dict:
        return {
            "type": "realtime",
            "model": self.settings.model,
            "instructions": self.settings.system_prompt,
            "include": ["item.input_audio_transcription.logprobs"],
            "max_output_tokens": self.settings.max_output_tokens,
            "output_modalities": ["audio"],
            "tool_choice": "auto",
            "tools": self.tools.as_openai_tools(),
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "noise_reduction": {"type": "far_field"},
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": self.settings.vad_threshold,
                        "prefix_padding_ms": self.settings.vad_prefix_padding_ms,
                        "silence_duration_ms": self.settings.vad_silence_duration_ms,
                        "create_response": self.settings.vad_create_response,
                        "interrupt_response": self.settings.vad_interrupt_response,
                    },
                    "transcription": self._build_transcription_config(
                        language=self.settings.transcription_language
                    ),
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "voice": self.settings.voice,
                },
            },
        }

    def _build_transcription_config(self, *, language: Optional[str]) -> dict:
        transcription_config = {"model": self.settings.transcription_model}
        if language is not None:
            transcription_config["language"] = language
        if self.settings.transcription_prompt:
            transcription_config["prompt"] = self.settings.transcription_prompt
        return transcription_config

    async def _send_microphone_audio(self, connection: AsyncRealtimeConnection) -> None:
        while True:
            chunk = await self.audio.read_input_chunk()
            if not self._should_send_microphone_audio():
                continue
            await connection.input_audio_buffer.append(audio=base64.b64encode(chunk).decode("ascii"))

    def _should_send_microphone_audio(self) -> bool:
        if self._sleep_farewell_pending or self._sleep_closing:
            return False

        if self.settings.allow_barge_in:
            return True

        if not self.settings.suppress_mic_while_playing:
            return True

        return not self.audio.is_output_active(self.settings.playback_gate_hold_ms)

    async def _send_initial_response(self, connection: AsyncRealtimeConnection) -> None:
        text = self.initial_response_text
        if text is None:
            return

        bundle = self.initial_action_bundle or self.orchestrator.on_wake(greeting_text=text)
        preapplied_wake_bundle = self.initial_action_bundle is not None
        speech = self._apply_action_bundle(
            bundle,
            include_motion=not self.wake_greeting_already_sent and not preapplied_wake_bundle,
            include_face=not preapplied_wake_bundle,
        )
        if speech is not None:
            text = speech.text
        response = _build_initial_response_config(
            text=text,
            max_output_tokens=FIXED_PHRASE_MAX_OUTPUT_TOKENS,
        )
        self.session_log.write("session", f"initial response: {text}")
        await connection.response.create(response=response)

    async def _handle_events(self, connection: AsyncRealtimeConnection) -> None:
        async for event in connection:
            event_type = event.type

            if event_type == "session.created":
                session_id = event.session.id or "<unknown>"
                self.logger.info("Session created: %s", session_id)
                self.session_log.write("session", f"created {session_id}")
                continue

            if event_type == "session.updated":
                self.logger.info("Session updated")
                self.session_log.write("session", "updated")
                continue

            if event_type == "response.created":
                self._handle_response_created_event(event.response)
                continue

            if event_type == "input_audio_buffer.speech_started":
                self._handle_speech_started_event()
                continue

            if event_type == "input_audio_buffer.speech_stopped":
                self._record_activity()
                self.logger.debug("Speech stopped")
                self.session_log.write("vad", "speech_stopped")
                continue

            if event_type == "conversation.item.input_audio_transcription.completed":
                await self._handle_user_transcription_completed(
                    connection,
                    event.transcript,
                    usage=getattr(event, "usage", None),
                    logprobs=getattr(event, "logprobs", None),
                )
                continue

            if event_type == "conversation.item.input_audio_transcription.failed":
                self._record_activity()
                message = event.error.message if event.error is not None else "unknown transcription error"
                self.logger.warning("Input transcription failed: %s", message)
                self.session_log.write("warning", f"input transcription failed: {message}")
                continue

            if event_type == "response.output_audio.delta":
                self._record_activity()
                response_id = getattr(event, "response_id", None)
                if self._should_ignore_response_output(response_id):
                    continue
                self._mark_assistant_output(response_id)
                self._ensure_speaking_face(response_id)
                self._ensure_speaking_motion(response_id)
                self.audio.enqueue_output(base64.b64decode(event.delta))
                continue

            if event_type == "response.output_audio_transcript.done":
                self._record_activity()
                response_id = getattr(event, "response_id", None)
                if self._should_ignore_response_output(response_id):
                    continue
                self._mark_assistant_output(response_id)
                self._set_assistant_transcript(response_id, event.transcript)
                print(event.transcript, flush=True)
                self.session_log.write("assistant", event.transcript)
                continue

            if event_type == "response.output_audio_transcript.delta":
                self._record_activity()
                response_id = getattr(event, "response_id", None)
                if self._should_ignore_response_output(response_id):
                    continue
                self._append_assistant_transcript(response_id, getattr(event, "delta", ""))
                continue

            if event_type == "response.output_text.done":
                self._record_activity()
                response_id = getattr(event, "response_id", None)
                if self._should_ignore_response_output(response_id):
                    continue
                self._mark_assistant_output(response_id)
                print(event.text, flush=True)
                self.session_log.write("assistant-text", event.text)
                continue

            if event_type == "response.function_call_arguments.done":
                self._record_activity()
                if self._should_ignore_response_output(getattr(event, "response_id", None)):
                    continue
                await self._handle_function_call(connection, event.response_id, event.call_id, event.name, event.arguments)
                continue

            if event_type == "response.done":
                await self._handle_response_done_event(connection, event.response)
                continue

            if event_type == "error":
                self.logger.error(
                    "Realtime error type=%s code=%s message=%s",
                    event.error.type,
                    event.error.code,
                    event.error.message,
                )
                self.session_log.write(
                    "error",
                    f"type={event.error.type} code={event.error.code} message={event.error.message}",
                )
                continue

    async def _handle_user_transcription_completed(
        self,
        connection: AsyncRealtimeConnection,
        transcript: str,
        *,
        usage: Optional[Any],
        logprobs: Optional[Any] = None,
    ) -> None:
        self._record_activity()
        if _is_transcription_prompt_echo(transcript, self.settings.transcription_prompt):
            self.session_log.write("user-ignored", f"transcription prompt echo: {transcript!r}")
            await self._send_transcription_retry(connection)
            return

        self.session_log.write("user", transcript)
        confidence = _get_transcription_confidence(logprobs)
        confidence_line = _format_transcription_confidence(confidence)
        if confidence_line is not None:
            self.session_log.write("transcription-confidence", confidence_line)
        if self.settings.print_user_transcripts:
            print(f"tu: {transcript}", flush=True)
        usage_line = _format_transcription_usage(usage)
        if usage_line is not None:
            usage_total = _get_usage_total_tokens(usage)
            if usage_total is not None:
                self._transcription_total_tokens += usage_total
                usage_line = f"{usage_line} session_transcription_total={self._transcription_total_tokens}"
            print(usage_line, flush=True)
            self.session_log.write("transcription-usage", usage_line)
        if confidence is not None and confidence[1] < LOW_CONFIDENCE_AVG_LOGPROB:
            self.session_log.write(
                "user-ignored",
                f"low confidence transcription avg_logprob={confidence[1]:.3f}",
            )
            await self._send_transcription_retry(connection)
            return

        if await self._maybe_enter_sleep_mode(connection, transcript):
            return
        self._maybe_run_contextual_motion(transcript)
        await self._create_normal_response(connection)

    async def _create_normal_response(self, connection: AsyncRealtimeConnection) -> None:
        instructions = (
            f"{self.settings.system_prompt}\n\n"
            f"{active_language_instruction(self._response_language)} "
            "Mantingues aquest idioma durant tot el torn."
        )
        await connection.response.create(response={"instructions": instructions})

    async def _change_active_language(
        self,
        connection: AsyncRealtimeConnection,
        language: str,
    ) -> None:
        self._response_language = language
        self.session_log.write("session", f"response language changed to {language}")
        session_update = {
            "type": "realtime",
            "audio": {
                "input": {
                    "transcription": self._build_transcription_config(language=language),
                }
            },
        }
        await connection.session.update(session=session_update)

    async def _send_transcription_retry(self, connection: AsyncRealtimeConnection) -> None:
        response = _build_initial_response_config(
            text=transcription_retry_text(self._response_language),
            max_output_tokens=FIXED_PHRASE_MAX_OUTPUT_TOKENS,
        )
        await connection.response.create(response=response)

    async def _handle_function_call(
        self,
        connection: AsyncRealtimeConnection,
        response_id: str,
        call_id: str,
        name: str,
        arguments_json: str,
    ) -> None:
        state = self._response_states.setdefault(response_id, ResponseState())
        state.pending_tools += 1
        arguments: dict[str, Any] = {}
        effective_name = name
        tool_executed = False

        self.logger.info("Tool call requested: %s(%s)", name, arguments_json)
        self.session_log.write("tool-call", f"{name} {arguments_json}")

        try:
            try:
                arguments = json.loads(arguments_json) if arguments_json else {}
            except json.JSONDecodeError as exc:
                tool_output = f"Tool input parsing failed for {name}: {exc}"
            else:
                effective_name, arguments = _resolve_tool_call_name_and_arguments(name, arguments)
                try:
                    self._notify_expressive_tool_intent(effective_name, arguments)
                    tool_output = self.tools.execute(effective_name, arguments)
                    if effective_name == "robot_set_language":
                        language = str(arguments.get("language", "")).strip().lower()
                        await self._change_active_language(connection, language)
                    tool_executed = True
                except ToolInputError as exc:
                    self.logger.warning("Tool input rejected for %s: %s", effective_name, exc)
                    tool_output = f"Tool input rejected for {effective_name}: {exc}"
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            self.logger.exception("Tool execution failed")
            tool_output = f"Tool execution failed for {effective_name}: {exc}"

        await connection.conversation.item.create(
            item={
                "type": "function_call_output",
                "call_id": call_id,
                "output": tool_output,
            }
        )
        state.outputs_sent += 1
        state.pending_tools -= 1
        if tool_executed and effective_name == "robot_set_face":
            if tool_output != NON_CRITICAL_FACE_FAILURE_MESSAGE:
                self._note_face_change(source="tool", expression=str(arguments.get("expression", "")))
        if tool_executed and effective_name in MOTION_TOOL_NAMES:
            gesture = str(arguments.get("gesture", arguments.get("movement", effective_name)))
            if effective_name == "robot_perform_gesture":
                self._record_motion_change(source="tool", gesture=gesture)
            else:
                self._note_motion_change(source="tool", gesture=gesture)

        self.session_log.write("tool-output", tool_output)
        self._record_activity()
        await self._maybe_continue(connection, response_id)

    def _notify_expressive_tool_intent(self, name: str, arguments: Mapping[str, Any]) -> None:
        if name == "robot_set_face":
            command = _validated_face_command(arguments)
            if command is not None:
                self.orchestrator.note_tool_face_intent(expression=command.normalized_expression)
            return

        if name == "robot_perform_gesture":
            command = _validated_gesture_command(arguments)
            if command is not None:
                self.orchestrator.note_tool_gesture_intent(gesture=command.normalized_gesture)

    def _ensure_speaking_face(self, response_id: Optional[str]) -> None:
        if response_id is None:
            return
        self._apply_action_bundle(self._orchestrator.on_assistant_audio_delta(response_id))

    def _ensure_speaking_motion(self, response_id: Optional[str]) -> None:
        if response_id is None:
            return
        self._apply_action_bundle(self._orchestrator.on_assistant_audio_delta(response_id))

    def _append_assistant_transcript(self, response_id: Optional[str], text: str) -> None:
        if response_id is None or not text:
            return

        self._apply_action_bundle(self._orchestrator.on_assistant_text_delta(response_id, text))

    def _set_assistant_transcript(self, response_id: Optional[str], text: str) -> None:
        if response_id is None:
            return

        self._apply_action_bundle(self._orchestrator.on_assistant_text_done(response_id, text))

    def _maybe_run_contextual_motion(self, transcript: str) -> None:
        self._apply_action_bundle(self._orchestrator.on_user_transcript(transcript))

    def _prepare_wake_greeting_motion(self) -> None:
        if self.wake_greeting_enabled:
            self._run_wake_greeting_motion()
            return

        if self.wake_greeting_already_sent and self.settings.auto_motion_events and self.motion is not None:
            self._note_motion_change(source="wake", gesture="greet_friendly")

    def _run_wake_greeting_motion(self) -> None:
        if not self.settings.auto_motion_events or self.motion is None:
            return

        self._note_motion_change(source="wake", gesture="greet_friendly")
        self._start_runtime_motion(gesture="greet_friendly", source="wake")

    def _warm_up_motion(self) -> None:
        if not self.settings.auto_motion_events or self.motion is None:
            return

        warm_up = getattr(self.motion, "warm_up", None)
        if not callable(warm_up):
            return

        try:
            result = warm_up()
        except Exception:
            self.logger.warning("Unable to warm up motion serial connection", exc_info=True)
            return

        self.session_log.write("motion", f"warmup result={result}")

    def _start_runtime_motion(self, *, gesture: str, source: str) -> None:
        thread = threading.Thread(
            target=self._run_runtime_motion,
            kwargs={"gesture": gesture, "source": source},
            name=f"robot-motion-{source}-{gesture}",
            daemon=True,
        )
        thread.start()

    def _apply_action_bundle(
        self,
        bundle: ActionBundle,
        *,
        include_motion: bool = True,
        include_face: bool = True,
    ) -> Optional[SpeechAction]:
        if include_face and bundle.face is not None:
            self._apply_face_action(bundle.face)
        if include_motion and bundle.motion is not None:
            self._apply_motion_action(bundle.motion)
        if bundle.session is not None:
            self._apply_session_action(bundle.session)
        return bundle.speech

    async def _apply_action_bundle_async(
        self,
        bundle: ActionBundle,
        *,
        include_motion: bool = True,
        include_face: bool = True,
    ) -> Optional[SpeechAction]:
        if include_face and bundle.face is not None:
            self._apply_face_action(bundle.face)
        if include_motion and bundle.motion is not None:
            if bundle.motion.blocking:
                await asyncio.to_thread(
                    self._run_blocking_motion,
                    gesture=bundle.motion.gesture,
                    source=bundle.motion.source,
                )
            else:
                self._apply_motion_action(bundle.motion)
        if bundle.session is not None:
            self._apply_session_action(bundle.session)
        return bundle.speech

    def _apply_session_action(self, action: SessionAction) -> None:
        if action.kind == "sleep_farewell_pending":
            self._sleep_farewell_pending = True
        elif action.kind == "close_after_sleep_farewell":
            self._sleep_close_requested = True

    def _apply_face_action(self, action: FaceAction) -> None:
        self._set_runtime_face(
            expression=action.expression,
            intensity=action.intensity,
            source=action.source,
            duration_ms=action.duration_ms,
        )

    def _apply_motion_action(self, action: MotionAction) -> None:
        self._record_motion_change(source=action.source, gesture=action.gesture)
        if action.blocking:
            self._run_blocking_motion(gesture=action.gesture, source=action.source)
            return
        self._start_runtime_motion(gesture=action.gesture, source=action.source)

    def _run_runtime_motion(self, *, gesture: str, source: str) -> None:
        if self.motion is None:
            return

        try:
            result = self.motion.gesture(gesture=gesture, wait_for_response=False)
        except Exception:
            self.logger.warning("Unable to run motion gesture from %s", source, exc_info=True)
            return

        self.session_log.write("motion", f"{source} gesture={gesture} result={result}")

    def _set_runtime_face(
        self,
        *,
        expression: str,
        intensity: int,
        source: str,
        duration_ms: Optional[int] = None,
    ) -> None:
        if not self.settings.auto_face_events or self.dual_eye is None:
            return

        try:
            result = self.dual_eye.set_face(expression=expression, intensity=intensity, duration_ms=duration_ms)
        except Exception:
            self.logger.warning("Unable to update Dual Eye face from %s", source, exc_info=True)
            return

        self._note_face_change(source=source, expression=expression)
        self.session_log.write("face", f"{source} expression={expression} result={result}")

    def _note_face_change(self, *, source: str, expression: str) -> None:
        del expression
        self._last_face_source = source
        self._last_face_at = time.monotonic()

    def _note_motion_change(self, *, source: str, gesture: str) -> None:
        self._orchestrator.note_motion(source=source, gesture=gesture)
        self._record_motion_change(source=source, gesture=gesture)

    def _record_motion_change(self, *, source: str, gesture: str) -> None:
        self._last_motion_source = source
        self._last_motion_gesture = gesture
        self._last_motion_at = time.monotonic()

    def _mark_assistant_output(self, response_id: Optional[str]) -> None:
        if response_id is None:
            return
        state = self._response_states.setdefault(response_id, ResponseState())
        state.assistant_output_sent = True

    async def _mark_response_done(self, connection: AsyncRealtimeConnection, response_id: str, status: str) -> None:
        state = self._response_states.setdefault(response_id, ResponseState())
        if status != "completed":
            self._clear_response_state(response_id)
            return
        state.response_done = True
        await self._maybe_continue(connection, response_id)

    async def _maybe_continue(self, connection: AsyncRealtimeConnection, response_id: str) -> None:
        state = self._response_states.get(response_id)
        if state is None:
            return

        if (
            state.response_done
            and state.pending_tools == 0
            and state.outputs_sent > 0
            and not state.assistant_output_sent
            and not state.continuation_requested
        ):
            state.continuation_requested = True
            self.logger.info("Continuing conversation after tool outputs for response %s", response_id)
            self.session_log.write("response", f"{response_id} continuation requested")
            await self._create_normal_response(connection)
            self._clear_response_state(response_id)
            return

        if state.response_done and state.pending_tools == 0:
            self._clear_response_state(response_id)

    def _clear_response_state(self, response_id: str) -> None:
        self._response_states.pop(response_id, None)
        self._orchestrator.clear_response(response_id)

    def _record_activity(self) -> None:
        self._last_activity_at = time.monotonic()

    def _handle_speech_started_event(self) -> None:
        self._record_activity()

        if (
            not self.settings.allow_barge_in
            and self.settings.suppress_mic_while_playing
            and self.audio.is_output_active(self.settings.playback_gate_hold_ms)
        ):
            self.logger.debug("Speech started ignored while robot output is active")
            self.session_log.write("vad", "speech_started_ignored_during_playback")
            return

        self._apply_action_bundle(self.orchestrator.on_user_speech_started())
        self.audio.clear_output()
        self.logger.debug("Speech started")
        self.session_log.write("vad", "speech_started")

    def _handle_response_created_event(self, response: Any) -> None:
        if self._sleep_farewell_pending and self._is_sleep_farewell_response(response):
            self._sleep_farewell_response_id = getattr(response, "id", None)

    async def _handle_response_done_event(self, connection: AsyncRealtimeConnection, response: Any) -> None:
        self._record_activity()
        response_id = getattr(response, "id", None) or "<unknown>"
        status = getattr(response, "status", "")
        ignored = self._should_ignore_response_output(response_id) and not self._is_sleep_farewell_response(response)

        self.logger.info("Response %s finished with status=%s", response_id, status)
        self.session_log.write("response", f"{response_id} status={status}")
        if not ignored:
            usage_line = _format_response_usage(response_id, getattr(response, "usage", None))
            if usage_line is not None:
                usage_total = _get_usage_total_tokens(getattr(response, "usage", None))
                if usage_total is not None:
                    self._response_total_tokens += usage_total
                    usage_line = f"{usage_line} session_response_total={self._response_total_tokens}"
                print(usage_line, flush=True)
                self.session_log.write("usage", usage_line)

        await self._mark_response_done(connection, response_id, status)
        if self._sleep_farewell_pending and self._is_sleep_farewell_response_done(response):
            await self._close_after_sleep_farewell(connection)

    def _should_ignore_response_output(self, response_id: Optional[str]) -> bool:
        if not self._sleep_farewell_pending:
            return False
        if self._sleep_farewell_response_id is None:
            return True
        return response_id != self._sleep_farewell_response_id

    def _is_sleep_farewell_response_done(self, response: Any) -> bool:
        response_id = getattr(response, "id", None)
        return self._is_sleep_farewell_response(response) or (
            response_id is not None and response_id == self._sleep_farewell_response_id
        )

    def _is_sleep_farewell_response(self, response: Any) -> bool:
        metadata = getattr(response, "metadata", None)
        if not isinstance(metadata, Mapping):
            return False
        return metadata.get("robot_poc_kind") == SLEEP_FAREWELL_METADATA["robot_poc_kind"]

    async def _maybe_enter_sleep_mode(self, connection: AsyncRealtimeConnection, transcript: str) -> bool:
        if not self.settings.sleep_word_enabled:
            return False

        phrase = match_sleep_word(transcript, self.settings.sleep_word_phrases)
        if phrase is None:
            return False

        message = f"sleep word detected phrase={phrase!r}; returning to wake word mode"
        self.logger.info(message)
        self.session_log.write("session", message)
        print("Sleep word detectada. Despidiéndose y volviendo al modo reposo.", flush=True)
        self.audio.clear_output()
        farewell_text = sleep_farewell_text(self._response_language)
        bundle = self.orchestrator.on_sleep_requested(farewell_text=farewell_text)
        speech = self._apply_action_bundle(bundle)
        await self._send_sleep_farewell(connection, speech=speech)
        return True

    async def _send_sleep_farewell(
        self,
        connection: AsyncRealtimeConnection,
        *,
        speech: Optional[SpeechAction] = None,
    ) -> None:
        text = speech.text if speech is not None else sleep_farewell_text(self._response_language)
        if self.settings.vad_create_response:
            await self._cancel_active_response_before_sleep_farewell(connection)
        response = _build_initial_response_config(
            text=text,
            max_output_tokens=FIXED_PHRASE_MAX_OUTPUT_TOKENS,
            metadata=SLEEP_FAREWELL_METADATA,
        )
        self.session_log.write("session", f"sleep farewell response: {text}")
        await connection.response.create(response=response)

    async def _cancel_active_response_before_sleep_farewell(self, connection: AsyncRealtimeConnection) -> None:
        cancel = getattr(connection.response, "cancel", None)
        if not callable(cancel):
            return

        try:
            await cancel()
        except Exception as exc:
            self.logger.debug("No active response to cancel before sleep farewell: %s", exc)

    async def _close_after_sleep_farewell(self, connection: AsyncRealtimeConnection) -> None:
        if self._sleep_closing:
            return

        self._sleep_closing = True
        await self._wait_for_output_to_finish(timeout_s=SLEEP_FAREWELL_PLAYBACK_TIMEOUT_S)
        await self._apply_action_bundle_async(self.orchestrator.on_sleep_farewell_finished())
        if not self._sleep_close_requested:
            self._sleep_closing = False
            return
        try:
            await connection.close(reason="sleep_word")
        except Exception as exc:
            self.logger.warning("Realtime connection did not close cleanly after sleep word: %s", exc)
        finally:
            self._sleep_farewell_pending = False
            self._sleep_farewell_response_id = None
            self._sleep_close_requested = False

    async def _wait_for_output_to_finish(self, *, timeout_s: float) -> None:
        deadline = time.monotonic() + max(timeout_s, 0.0)
        while self.audio.is_output_active(hold_ms=100):
            if time.monotonic() >= deadline:
                self.logger.warning("Timed out waiting for farewell audio playback to finish")
                return
            await asyncio.sleep(0.05)

    def _run_sleep_greeting_motion(self) -> None:
        self._run_blocking_motion(gesture="greet_friendly", source="sleep_word")

    def _run_sleep_rest_motion(self) -> None:
        self._run_blocking_motion(gesture="sleep", source="sleep_word")

    def _run_blocking_motion(self, *, gesture: str, source: str) -> None:
        result = _run_motion_gesture_safely(self.motion, gesture=gesture, source=source, logger=self.logger)
        if result is None:
            return
        self.session_log.write("motion", f"{source} gesture={gesture} result={result}")

    def _build_idle_watcher(self, connection: AsyncRealtimeConnection) -> Optional[asyncio.Task[None]]:
        if self.settings.active_idle_timeout_s <= 0:
            return None
        return asyncio.create_task(self._close_when_idle(connection))

    async def _close_when_idle(self, connection: AsyncRealtimeConnection) -> None:
        timeout_s = self.settings.active_idle_timeout_s
        while True:
            await asyncio.sleep(min(1.0, timeout_s))
            if self.audio.is_output_active(self.settings.playback_gate_hold_ms):
                self._record_activity()
                continue
            idle_for = time.monotonic() - self._last_activity_at
            if idle_for < timeout_s:
                continue

            message = f"idle timeout after {timeout_s:.1f}s; returning to wake word mode"
            self.logger.info(message)
            self.session_log.write("session", message)
            await connection.close(reason="idle_timeout")
            return


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Quibot voice interaction using OpenAI Realtime API")
    parser.add_argument("--list-devices", action="store_true", help="List available audio devices and exit")
    parser.add_argument("--test-wake-word", action="store_true", help="Wait for the local wake word and exit")
    parser.add_argument(
        "--debug-wake-word",
        action="store_true",
        help="Print local wake word recognizer partial/final transcripts while waiting",
    )
    parser.add_argument(
        "--test-motion",
        choices=[*SUPPORTED_DIRECTIONS, *SUPPORTED_GESTURE_INPUTS, "stop"],
        help="Send one movement or gesture command to the Arduino motion controller and exit",
    )
    parser.add_argument(
        "--test-motion-steps",
        type=int,
        default=1,
        help="Movement units for --test-motion forward/backward/left/right",
    )
    parser.add_argument(
        "--test-face",
        choices=SUPPORTED_EXPRESSIONS,
        help="Send one DualEye face command and exit",
    )
    return parser


def _format_response_usage(response_id: str, usage: Optional[Any]) -> Optional[str]:
    if usage is None:
        return None

    parts = [f"tokens response_id={response_id}"]
    _append_usage_count(parts, "input", getattr(usage, "input_tokens", None))
    _append_usage_count(parts, "output", getattr(usage, "output_tokens", None))
    _append_usage_count(parts, "total", getattr(usage, "total_tokens", None))

    input_details = getattr(usage, "input_token_details", None)
    if input_details is not None:
        _append_usage_count(parts, "input_text", getattr(input_details, "text_tokens", None))
        _append_usage_count(parts, "input_audio", getattr(input_details, "audio_tokens", None))
        _append_usage_count(parts, "input_cached", getattr(input_details, "cached_tokens", None))

    output_details = getattr(usage, "output_token_details", None)
    if output_details is not None:
        _append_usage_count(parts, "output_text", getattr(output_details, "text_tokens", None))
        _append_usage_count(parts, "output_audio", getattr(output_details, "audio_tokens", None))

    return " ".join(parts)


def _format_transcription_usage(usage: Optional[Any]) -> Optional[str]:
    if usage is None:
        return None

    usage_type = getattr(usage, "type", None)
    if usage_type == "duration":
        seconds = getattr(usage, "seconds", None)
        if seconds is None:
            return None
        return f"transcription usage seconds={seconds}"

    parts = ["tokens transcription"]
    _append_usage_count(parts, "input", getattr(usage, "input_tokens", None))
    _append_usage_count(parts, "output", getattr(usage, "output_tokens", None))
    _append_usage_count(parts, "total", getattr(usage, "total_tokens", None))

    input_details = getattr(usage, "input_token_details", None)
    if input_details is not None:
        _append_usage_count(parts, "input_text", getattr(input_details, "text_tokens", None))
        _append_usage_count(parts, "input_audio", getattr(input_details, "audio_tokens", None))

    if len(parts) == 1:
        return None
    return " ".join(parts)


def _get_transcription_confidence(logprobs: Optional[Any]) -> Optional[tuple[int, float, float]]:
    if not logprobs:
        return None

    values = []
    for entry in logprobs:
        value = entry.get("logprob") if isinstance(entry, Mapping) else getattr(entry, "logprob", None)
        if isinstance(value, (int, float)):
            values.append(float(value))

    if not values:
        return None

    average = sum(values) / len(values)
    return len(values), average, min(values)


def _format_transcription_confidence(confidence: Optional[tuple[int, float, float]]) -> Optional[str]:
    if confidence is None:
        return None
    token_count, average, minimum = confidence
    return f"tokens={token_count} avg_logprob={average:.3f} min_logprob={minimum:.3f}"


def _is_transcription_prompt_echo(transcript: str, transcription_prompt: str) -> bool:
    normalized_transcript = normalize_text(transcript)
    normalized_prompt = normalize_text(transcription_prompt)
    if not normalized_transcript or not normalized_prompt:
        return False
    if normalized_transcript == normalized_prompt:
        return True
    if (
        "conversa en catala amb un robot humanoide educatiu" in normalized_transcript
        and "paraules i frases probables" in normalized_transcript
    ):
        return True

    transcript_words = normalized_transcript.split()
    prompt_words = set(normalized_prompt.split())
    if len(transcript_words) < 8:
        return False
    overlap = sum(word in prompt_words for word in transcript_words)
    return overlap / len(transcript_words) >= 0.8


def _append_usage_count(parts: list[str], label: str, value: Optional[int]) -> None:
    if value is not None:
        parts.append(f"{label}={value}")


def _get_usage_total_tokens(usage: Optional[Any]) -> Optional[int]:
    if usage is None:
        return None
    return getattr(usage, "total_tokens", None)


def _resolve_tool_call_name_and_arguments(
    name: str,
    arguments: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    if name != "functions":
        return name, dict(arguments)

    nested_name = _first_string_value(arguments, ("name", "tool", "tool_name", "function_name"))
    nested_arguments: Any = arguments.get("arguments", arguments.get("parameters", arguments.get("args", {})))

    function_payload = arguments.get("function")
    if isinstance(function_payload, Mapping):
        nested_name = _first_string_value(
            function_payload,
            ("name", "tool", "tool_name", "function_name"),
        ) or nested_name
        nested_arguments = function_payload.get(
            "arguments",
            function_payload.get("parameters", function_payload.get("args", nested_arguments)),
        )
    elif isinstance(function_payload, str) and nested_name is None:
        nested_name = function_payload

    if nested_name is None:
        return name, dict(arguments)

    return nested_name, _coerce_tool_arguments(nested_arguments)


def _validated_face_command(arguments: Mapping[str, Any]) -> Optional[FaceCommand]:
    try:
        intensity = int(arguments.get("intensity", 3))
        duration_ms_raw = arguments.get("duration_ms")
        duration_ms = int(duration_ms_raw) if duration_ms_raw is not None else None
        command = FaceCommand(
            expression=str(arguments.get("expression", "neutral")),
            intensity=intensity,
            duration_ms=duration_ms,
        )
        command.validate()
    except (TypeError, ValueError):
        return None
    return command


def _validated_gesture_command(arguments: Mapping[str, Any]) -> Optional[GestureCommand]:
    command = GestureCommand(gesture=str(arguments.get("gesture", "explain")))
    try:
        command.validate()
    except ValueError:
        return None
    return command


def _first_string_value(arguments: Mapping[str, Any], names: tuple[str, ...]) -> Optional[str]:
    for candidate_name in names:
        value = arguments.get(candidate_name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _coerce_tool_arguments(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _run_motion_gesture_safely(
    motion: Optional[MotionClient],
    *,
    gesture: str,
    source: str,
    logger: Optional[logging.Logger] = None,
) -> Optional[str]:
    if motion is None:
        return None

    active_logger = logger or logging.getLogger(__name__)
    try:
        return motion.gesture(gesture=gesture, wait_for_response=False)
    except Exception:
        active_logger.warning("Failed to run %s gesture from %s", gesture, source, exc_info=True)
        return None


async def run_cli(args: argparse.Namespace) -> int:
    if args.list_devices:
        print(list_audio_devices())
        return 0

    settings = Settings.from_env()
    if args.test_motion:
        return _test_motion(settings, command=args.test_motion, steps=args.test_motion_steps)

    if args.test_face:
        return _test_face(settings, expression=args.test_face)

    if args.test_wake_word:
        return await _test_wake_word(settings, debug=args.debug_wake_word)

    if settings.wake_word_enabled:
        return await _run_with_wake_word(settings, debug=args.debug_wake_word)

    dual_eye = _build_dual_eye_client(settings)
    motion = _build_motion_client(settings)
    app = RobotVoiceApp(
        settings=settings,
        tools=build_default_tool_registry(dual_eye=dual_eye, motion=motion),
        dual_eye=dual_eye,
        motion=motion,
    )
    await app.run()
    return 0


async def _test_wake_word(settings: Settings, *, debug: bool = False) -> int:
    try:
        listener = _build_wake_word_listener(settings, debug=debug)
    except WakeWordError as exc:
        print(f"Wake word configuration error: {exc}", flush=True)
        return 2

    print("Esperando wake word local. No se usa OpenAI en este modo.", flush=True)
    if debug:
        print("Debug wake word activo: se imprimira lo que Vosk vaya oyendo.", flush=True)
    result = await asyncio.to_thread(listener.wait_for_wake_word)
    print(
        f"Wake word detectada: phrase={result.phrase!r} transcript={result.transcript!r} source={result.source}",
        flush=True,
    )
    return 0


def _test_motion(settings: Settings, *, command: str, steps: int) -> int:
    motion = _build_motion_client(settings)
    try:
        if command in SUPPORTED_DIRECTIONS:
            result = motion.move(direction=command, units=steps)
        elif command == "stop":
            result = motion.stop()
        else:
            result = motion.gesture(gesture=command)
    except ValueError as exc:
        print(f"Motion command error: {exc}", flush=True)
        return 2
    finally:
        motion.close()

    print(result, flush=True)
    return 0


def _test_face(settings: Settings, *, expression: str) -> int:
    dual_eye = _build_dual_eye_client(settings)
    try:
        try:
            result = dual_eye.set_face(expression=expression, intensity=3, duration_ms=None)
        except ValueError as exc:
            print(f"Face command error: {exc}", flush=True)
            return 2
        print(result, flush=True)
        if isinstance(result, str) and result.startswith(NON_CRITICAL_DUAL_EYE_FAILURE_PREFIXES):
            return 1
        return 0
    finally:
        dual_eye.close()


async def _run_with_wake_word(settings: Settings, *, debug: bool = False) -> int:
    try:
        listener = _build_wake_word_listener(settings, debug=debug)
    except WakeWordError as exc:
        print(f"Wake word configuration error: {exc}", flush=True)
        return 2

    dual_eye = _build_dual_eye_client(settings)
    motion = _build_motion_client(settings)
    try:
        while True:
            print("Modo dormido: esperando wake word local...", flush=True)
            _set_dual_eye_face_safely(dual_eye, expression="sleepy", intensity=2)
            result = await asyncio.to_thread(listener.wait_for_wake_word)
            print(f"Wake word detectada: {result.phrase!r}. Activando IA...", flush=True)
            orchestrator = RobotOrchestrator(
                auto_face_events=settings.auto_face_events,
                auto_motion_events=settings.auto_motion_events,
                face_enabled=dual_eye is not None,
                motion_enabled=motion is not None,
            )
            wake_bundle = orchestrator.on_wake(greeting_text=settings.wake_ack_text)
            app = RobotVoiceApp(
                settings=settings,
                tools=build_default_tool_registry(dual_eye=dual_eye, motion=motion),
                dual_eye=dual_eye,
                motion=motion,
                close_dual_eye_on_exit=False,
                close_motion_on_exit=False,
                initial_response_text=settings.wake_ack_text if settings.wake_ack_enabled else None,
                orchestrator=orchestrator,
                initial_action_bundle=wake_bundle,
            )
            await app._apply_action_bundle_async(wake_bundle)
            if settings.wake_word_activation_delay_ms > 0:
                await asyncio.sleep(settings.wake_word_activation_delay_ms / 1000)
            try:
                await app.run()
            except ConnectionClosed as exc:
                if not _is_expected_realtime_close(exc):
                    raise
                logging.getLogger(__name__).info(
                    "Realtime session closed normally (%s); returning to wake word mode",
                    exc,
                )
    finally:
        dual_eye.close()
        motion.close()


def _build_dual_eye_client(settings: Settings) -> DualEyeClient:
    return DualEyeClient(
        endpoint=settings.dual_eye_endpoint,
        serial_port=settings.dual_eye_serial_port,
        serial_baudrate=settings.dual_eye_serial_baudrate,
        timeout_s=settings.dual_eye_timeout_s,
        serial_write_timeout_s=settings.dual_eye_serial_write_timeout_s,
        serial_startup_delay_s=settings.dual_eye_serial_startup_delay_s,
        logger=logging.getLogger("robot_poc.dual_eye"),
    )


def _build_motion_client(settings: Settings) -> MotionClient:
    return MotionClient(
        serial_port=settings.motion_serial_port,
        serial_baudrate=settings.motion_serial_baudrate,
        timeout_s=settings.motion_timeout_s,
        serial_write_timeout_s=settings.motion_serial_write_timeout_s,
        serial_startup_delay_s=settings.motion_serial_startup_delay_s,
        logger=logging.getLogger("robot_poc.motion"),
    )


def _build_wake_word_listener(settings: Settings, *, debug: bool = False) -> VoskWakeWordListener:
    if settings.wake_word_model_path is None:
        raise WakeWordError("ROBOT_POC_WAKE_WORD_MODEL_PATH is required when wake word mode is enabled")

    return VoskWakeWordListener(
        model_path=settings.wake_word_model_path,
        phrases=settings.wake_word_phrases,
        input_device=settings.input_device,
        sample_rate=settings.wake_word_sample_rate,
        block_ms=settings.wake_word_block_ms,
        logger=logging.getLogger("robot_poc.wake_word"),
        debug_callback=_print_wake_word_debug if debug else None,
    )


def _print_wake_word_debug(source: str, transcript: str) -> None:
    print(f"wake-word {source}: {transcript}", flush=True)


def _build_initial_response_config(
    *,
    text: str,
    max_output_tokens: int,
    metadata: Optional[Mapping[str, str]] = None,
) -> dict:
    exact_text = text.strip()
    config = {
        "conversation": "none",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": f'Text exacte que has de dir en veu alta: "{exact_text}"',
                    }
                ],
            }
        ],
        "instructions": (
            "Ets un motor de veu de Quibot. Digues nomes el text exacte entre cometes. "
            f'"{exact_text}" '
            "No tradueixis, no responguis la frase, no afegeixis cap paraula i no facis servir cap tool."
        ),
        "max_output_tokens": max_output_tokens,
        "output_modalities": ["audio"],
        "tool_choice": "none",
        "tools": [],
    }
    if metadata is not None:
        config["metadata"] = dict(metadata)
    return config


def _set_dual_eye_face_safely(
    dual_eye: DualEyeClient,
    *,
    expression: str,
    intensity: int,
    duration_ms: Optional[int] = None,
) -> None:
    try:
        dual_eye.set_face(expression=expression, intensity=intensity, duration_ms=duration_ms)
    except Exception:
        logging.getLogger(__name__).warning("Unable to update Dual Eye face", exc_info=True)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.list_devices:
            configure_logging("INFO")
        else:
            configure_logging(Settings.from_env().log_level)
        return asyncio.run(run_cli(args))
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", flush=True)
        return 2
    except KeyboardInterrupt:
        print("\nSaliendo.", flush=True)
        return 0

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

from .wake_word import normalize_text


CONTEXT_MOTION_SUPPRESS_EXPLAIN_S = 4.0
MIN_ASSISTANT_TRANSCRIPT_CHARS_FOR_EXPLAIN = 18

CONTEXTUAL_MOTION_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("emotion", ("celebra", "celebris", "celebrar", "celebracio", "celebracion", "mostra emocio", "muestra emocion", "fes emocio", "haz emocion")),
    ("exactly", ("assenyala", "assenyalar", "senyala", "senyalar", "senala", "senalar", "apunta", "apuntar")),
    ("greet_friendly", ("hola", "bon dia", "buenos dias", "bona tarda", "buenas tardes", "adeu", "a deu", "adios", "fins aviat", "fins despres", "hasta luego", "bye")),
)

ASSISTANT_RESPONSE_MOTION_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("exactly", ("exacte", "exacto", "exactament", "exactamente", "correcte", "correcto", "molt be", "muy bien", "bona pregunta", "buena pregunta", "ben fet", "bien hecho", "has encertat", "has acertado", "ho has encertat", "lo has acertado", "resposta correcta", "respuesta correcta", "si aixo es", "si eso es")),
    ("emotion", ("genial", "fantastic", "fantastico", "bravo", "enhorabona", "felicitats", "felicidades", "me n alegro", "m alegro", "me alegro", "que be", "que alegria", "estic content", "estoy contento", "ho celebro", "m encanta", "me encanta")),
)


@dataclass(frozen=True)
class FaceAction:
    expression: str
    intensity: int = 3
    duration_ms: Optional[int] = None
    source: str = ""


@dataclass(frozen=True)
class MotionAction:
    gesture: str
    source: str
    wait_for_response: bool = False
    blocking: bool = False


@dataclass(frozen=True)
class SpeechAction:
    text: str
    kind: str = "fixed"


@dataclass(frozen=True)
class SessionAction:
    kind: str


@dataclass(frozen=True)
class ActionBundle:
    face: Optional[FaceAction] = None
    motion: Optional[MotionAction] = None
    speech: Optional[SpeechAction] = None
    after_speech_motion: Optional[MotionAction] = None
    session: Optional[SessionAction] = None

    @property
    def is_empty(self) -> bool:
        return (
            self.face is None
            and self.motion is None
            and self.speech is None
            and self.after_speech_motion is None
            and self.session is None
        )


@dataclass
class ResponseBehaviorState:
    speaking_face_applied: bool = False
    speaking_motion_applied: bool = False
    speaking_motion_source: str = ""
    assistant_transcript_text: str = ""


def select_contextual_motion(transcript: str) -> Optional[str]:
    normalized = normalize_text(transcript)
    if not normalized:
        return None
    padded = f" {normalized} "
    for gesture, phrases in CONTEXTUAL_MOTION_RULES:
        if any(_contains_normalized_phrase(padded, phrase) for phrase in phrases):
            return gesture
    return None


def select_assistant_response_motion(transcript: str) -> Optional[str]:
    normalized = normalize_text(transcript)
    if not normalized:
        return None
    padded = f" {normalized} "
    for gesture, phrases in ASSISTANT_RESPONSE_MOTION_RULES:
        if any(_contains_normalized_phrase(padded, phrase) for phrase in phrases):
            return gesture
    return None


def _contains_normalized_phrase(padded_normalized_text: str, phrase: str) -> bool:
    return f" {phrase} " in padded_normalized_text


class RobotOrchestrator:
    def __init__(
        self,
        *,
        auto_face_events: bool = True,
        auto_motion_events: bool = True,
        face_enabled: bool = True,
        motion_enabled: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.auto_face_events = auto_face_events
        self.auto_motion_events = auto_motion_events
        self.face_enabled = face_enabled
        self.motion_enabled = motion_enabled
        self._clock = clock
        self._last_face_at = 0.0
        self._last_face_source = ""
        self._last_motion_at = 0.0
        self._last_motion_source = ""
        self._last_motion_gesture = ""
        self._response_states: Dict[str, ResponseBehaviorState] = {}

    def on_wake(self, *, greeting_text: str) -> ActionBundle:
        face = None
        if self.auto_face_events and self.face_enabled:
            face = FaceAction(expression="happy", intensity=3, duration_ms=800, source="wake")
            self.note_face(source="wake", expression="happy")

        motion = None
        if self.auto_motion_events and self.motion_enabled:
            motion = MotionAction(
                gesture="greet_friendly",
                source="wake",
                wait_for_response=False,
                blocking=True,
            )
            self.note_motion(source="wake", gesture="greet_friendly")

        return ActionBundle(
            face=face,
            motion=motion,
            speech=SpeechAction(text=greeting_text, kind="wake_ack"),
        )

    def on_user_speech_started(self) -> ActionBundle:
        if not self.auto_face_events or not self.face_enabled:
            return ActionBundle()
        self.note_face(source="listening", expression="thinking")
        return ActionBundle(face=FaceAction(expression="thinking", intensity=2, source="listening"))

    def on_sleep_requested(self, *, farewell_text: str) -> ActionBundle:
        face = None
        if self.face_enabled:
            face = FaceAction(expression="sleepy", intensity=2, source="sleep_word")
            self.note_face(source="sleep_word", expression="sleepy")

        motion = None
        if self.motion_enabled:
            motion = MotionAction(
                gesture="greet_friendly",
                source="sleep_word",
                wait_for_response=False,
                blocking=True,
            )
            self.note_motion(source="sleep_word", gesture="greet_friendly")

        return ActionBundle(
            face=face,
            motion=motion,
            speech=SpeechAction(text=farewell_text, kind="sleep_farewell"),
            session=SessionAction(kind="sleep_farewell_pending"),
        )

    def on_sleep_farewell_finished(self) -> ActionBundle:
        motion = None
        if self.motion_enabled:
            motion = MotionAction(
                gesture="sleep",
                source="sleep_word",
                wait_for_response=False,
                blocking=True,
            )
            self.note_motion(source="sleep_word", gesture="sleep")
        return ActionBundle(
            motion=motion,
            session=SessionAction(kind="close_after_sleep_farewell"),
        )

    def on_user_transcript(self, transcript: str) -> ActionBundle:
        if not self.auto_motion_events or not self.motion_enabled:
            return ActionBundle()

        gesture = select_contextual_motion(transcript)
        if gesture is None:
            return ActionBundle()

        self.note_motion(source="context", gesture=gesture)
        return ActionBundle(motion=MotionAction(gesture=gesture, source="context", wait_for_response=False))

    def on_assistant_text_delta(self, response_id: str, text: str) -> ActionBundle:
        if not text:
            return ActionBundle()
        state = self._response_states.setdefault(response_id, ResponseBehaviorState())
        state.assistant_transcript_text += text
        return self._maybe_run_assistant_response_motion(response_id)

    def on_assistant_text_done(self, response_id: str, text: str) -> ActionBundle:
        state = self._response_states.setdefault(response_id, ResponseBehaviorState())
        state.assistant_transcript_text = text
        return self._maybe_run_assistant_response_motion(response_id)

    def on_assistant_audio_delta(self, response_id: str) -> ActionBundle:
        state = self._response_states.setdefault(response_id, ResponseBehaviorState())
        face = self._select_speaking_face(state)
        motion = self._select_speaking_motion(response_id, state)
        return ActionBundle(face=face, motion=motion)

    def _select_speaking_face(self, state: ResponseBehaviorState) -> Optional[FaceAction]:
        if not self.auto_face_events or not self.face_enabled:
            return None
        if state.speaking_face_applied:
            return None
        if self._last_face_source == "tool" and self._clock() - self._last_face_at < 3.0:
            state.speaking_face_applied = True
            return None
        state.speaking_face_applied = True
        self.note_face(source="speaking_default", expression="happy")
        return FaceAction(expression="happy", intensity=2, source="speaking_default")

    def _select_speaking_motion(
        self,
        response_id: str,
        state: ResponseBehaviorState,
    ) -> Optional[MotionAction]:
        del response_id
        if not self.auto_motion_events or not self.motion_enabled:
            return None
        if state.speaking_motion_applied:
            return None
        if self._recent_context_or_tool_motion():
            state.speaking_motion_applied = True
            state.speaking_motion_source = self._last_motion_source
            return None
        if len(normalize_text(state.assistant_transcript_text)) < MIN_ASSISTANT_TRANSCRIPT_CHARS_FOR_EXPLAIN:
            return None
        state.speaking_motion_applied = True
        state.speaking_motion_source = "speaking_default"
        self.note_motion(source="speaking_default", gesture="explain")
        return MotionAction(gesture="explain", source="speaking_default", wait_for_response=False)

    def note_tool_face(
        self,
        *,
        expression: str,
        tool_output: str,
        non_critical_failure_message: str,
    ) -> None:
        if tool_output == non_critical_failure_message:
            return
        self.note_face(source="tool", expression=expression)

    def note_tool_face_intent(self, *, expression: str) -> None:
        self.note_face(source="tool", expression=expression)

    def note_tool_gesture_intent(self, *, gesture: str) -> None:
        self.note_motion(source="tool", gesture=gesture)

    def clear_response(self, response_id: str) -> None:
        self._response_states.pop(response_id, None)

    def _maybe_run_assistant_response_motion(self, response_id: str) -> ActionBundle:
        if not self.auto_motion_events or not self.motion_enabled:
            return ActionBundle()

        state = self._response_states.setdefault(response_id, ResponseBehaviorState())
        if state.speaking_motion_applied:
            return ActionBundle()
        if not state.speaking_motion_applied and self._recent_context_or_tool_motion():
            state.speaking_motion_applied = True
            state.speaking_motion_source = self._last_motion_source
            return ActionBundle()

        gesture = select_assistant_response_motion(state.assistant_transcript_text)
        if gesture is None:
            return ActionBundle()

        state.speaking_motion_applied = True
        state.speaking_motion_source = "assistant_response"
        self.note_motion(source="assistant_response", gesture=gesture)
        return ActionBundle(
            motion=MotionAction(gesture=gesture, source="assistant_response", wait_for_response=False)
        )

    def _recent_context_or_tool_motion(self) -> bool:
        if self._last_motion_source not in {"assistant_response", "context", "tool", "wake"}:
            return False
        return self._clock() - self._last_motion_at < CONTEXT_MOTION_SUPPRESS_EXPLAIN_S

    def note_face(self, *, source: str, expression: str) -> None:
        del expression
        self._last_face_source = source
        self._last_face_at = self._clock()

    def note_motion(self, *, source: str, gesture: str) -> None:
        self._last_motion_source = source
        self._last_motion_gesture = gesture
        self._last_motion_at = self._clock()

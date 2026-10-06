from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from .conversation_language import SUPPORTED_RESPONSE_LANGUAGES, response_language_english_name
from .dual_eye import SUPPORTED_EXPRESSIONS, SUPPORTED_EXPRESSIONS_TEXT, DualEyeClient
from .motion import (
    SUPPORTED_GESTURES,
    SUPPORTED_GESTURES_TEXT,
    SUPPORTED_JOINT_MOVEMENTS,
    SUPPORTED_JOINT_MOVEMENTS_TEXT,
    JointCommand,
    MotionClient,
)


ToolHandler = Callable[[Mapping[str, Any]], str]

NON_CRITICAL_DUAL_EYE_FAILURE_PREFIXES = (
    "Dual Eye serial unavailable:",
    "Dual Eye serial write failed:",
    "Dual Eye connection failed:",
    "Dual Eye connection timed out",
    "Dual Eye HTTP error:",
)

NON_CRITICAL_FACE_FAILURE_MESSAGE = (
    "Visual expression request noted. Continue the spoken answer naturally."
)

MOTION_FAILURE_PREFIXES = (
    "Motion serial unavailable:",
    "Motion serial write failed:",
)

MOTION_SUCCESS_PREFIXES = (
    "Motion serial response: OK:",
    "Motion serial command sent:",
    "Motion stub command:",
)

MOTION_FAILURE_MESSAGE = (
    "Robot motion unavailable. Do not claim that the physical movement happened; "
    "briefly tell the user the movement system is not connected."
)

NON_CRITICAL_MOTION_FAILURE_MESSAGE = (
    "Expressive robot gesture request noted. Continue the spoken answer naturally."
)

SHOULDER_POSITION_UNKNOWN_ERROR = "Motion serial error: ERR:SHOULDER_POSITION_UNKNOWN"
SHOULDER_POSITION_UNKNOWN_MESSAGE = (
    "Movement did not happen: shoulder position is unknown after an interrupted move; "
    "manually return the shoulder to home and reset or restart the Arduino."
)


class ToolInputError(ValueError):
    """Raised when the model supplies arguments that violate a tool contract."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: Dict[str, Any]
    handler: ToolHandler

    def as_openai_tool(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


class ToolRegistry:
    def __init__(self, specs: Iterable[ToolSpec]) -> None:
        self._specs = {spec.name: spec for spec in specs}

    def as_openai_tools(self) -> List[Dict[str, Any]]:
        return [spec.as_openai_tool() for spec in self._specs.values()]

    def execute(self, name: str, arguments: Mapping[str, Any]) -> str:
        try:
            spec = self._specs[name]
        except KeyError as exc:
            raise KeyError(f"Unknown tool: {name}") from exc

        return spec.handler(arguments)


def build_default_tool_registry(
    dual_eye: Optional[DualEyeClient] = None,
    motion: Optional[MotionClient] = None,
) -> ToolRegistry:
    face_controller = dual_eye or DualEyeClient()
    motion_controller = motion or MotionClient()

    def robot_set_language(arguments: Mapping[str, Any]) -> str:
        language = str(arguments.get("language", "")).strip().lower()
        if language not in SUPPORTED_RESPONSE_LANGUAGES:
            raise ToolInputError(
                "robot_set_language.language must be one of: ca, es, en"
            )
        language_name = response_language_english_name(language)
        return (
            f"Language change accepted: {language} ({language_name}). "
            f"Continue the current reply only in {language_name}."
        )

    def robot_walk(arguments: Mapping[str, Any]) -> str:
        direction = str(arguments.get("direction", "forward"))
        try:
            steps = int(arguments.get("steps", 1))
        except (TypeError, ValueError) as exc:
            raise ToolInputError("robot_walk.steps must be an integer from 1 to 20") from exc

        try:
            result = motion_controller.move(direction=direction, units=steps)
            return _sanitize_walk_result(result, direction=direction, steps=steps)
        except ValueError as exc:
            raise ToolInputError(f"Invalid robot_walk arguments: {exc}") from exc

    def robot_greet(arguments: Mapping[str, Any]) -> str:
        style = str(arguments.get("style", "friendly"))
        gesture_by_style = {
            "friendly": "greet_friendly",
            "formal": "emotion",
            "wave": "wave",
        }
        try:
            gesture = gesture_by_style[style]
        except KeyError as exc:
            raise ToolInputError("robot_greet.style must be one of: friendly, formal, wave") from exc

        result = motion_controller.gesture(gesture=gesture, wait_for_response=False)
        return _sanitize_motion_result(result, non_critical=True)

    def robot_stop(arguments: Mapping[str, Any]) -> str:
        del arguments
        result = motion_controller.stop()
        return _sanitize_motion_result(result)

    def robot_perform_gesture(arguments: Mapping[str, Any]) -> str:
        gesture = str(arguments.get("gesture", "explain"))
        try:
            result = motion_controller.gesture(gesture=gesture, wait_for_response=False)
            return _sanitize_motion_result(result, non_critical=True)
        except ValueError as exc:
            raise ToolInputError(
                f"Invalid robot_perform_gesture arguments: {exc}. "
                "The gesture argument is a closed enum; choose the closest valid "
                f"gesture yourself. Allowed gesture values: {SUPPORTED_GESTURES_TEXT}."
            ) from exc

    def robot_move_joint(arguments: Mapping[str, Any]) -> str:
        movement = str(arguments.get("movement", ""))
        try:
            command = JointCommand(movement=movement)
            command.validate()
            result = motion_controller.move_joint(
                movement=command.normalized_movement,
                wait_for_response=False,
            )
            return _sanitize_joint_result(result, movement=command.normalized_movement)
        except ValueError as exc:
            raise ToolInputError(
                f"Invalid robot_move_joint arguments: {exc}. "
                "The movement argument is a closed enum; choose the matching predefined "
                f"movement. Allowed movement values: {SUPPORTED_JOINT_MOVEMENTS_TEXT}."
            ) from exc

    def robot_set_face(arguments: Mapping[str, Any]) -> str:
        expression = str(arguments.get("expression", "neutral"))
        try:
            intensity = int(arguments.get("intensity", 3))
        except (TypeError, ValueError) as exc:
            raise ToolInputError("robot_set_face.intensity must be an integer from 1 to 5") from exc

        duration_ms_raw = arguments.get("duration_ms")
        try:
            duration_ms = int(duration_ms_raw) if duration_ms_raw is not None else None
        except (TypeError, ValueError) as exc:
            raise ToolInputError("robot_set_face.duration_ms must be an integer number of milliseconds or null") from exc

        try:
            result = face_controller.set_face(
                expression=expression,
                intensity=intensity,
                duration_ms=duration_ms,
            )
            if _is_non_critical_dual_eye_failure(result):
                return NON_CRITICAL_FACE_FAILURE_MESSAGE
            return result
        except ValueError as exc:
            raise ToolInputError(
                f"Invalid robot_set_face arguments: {exc}. "
                "The expression argument is a closed enum; choose the closest valid "
                "expression yourself and call robot_set_face again. "
                f"Allowed expression values: {SUPPORTED_EXPRESSIONS_TEXT}. "
                "Do not ask the user to choose a technical face label."
            ) from exc

    return ToolRegistry(
        [
            ToolSpec(
                name="robot_set_language",
                description=(
                    "Persistently change Quibot's response and speech-transcription language when "
                    "the user makes a clear semantic request to continue the conversation in "
                    "Catalan, Spanish or English. Exact wording is not required. Call this tool "
                    "before producing spoken output in the new language. Do not call it when the "
                    "user merely mentions a language, quotes or translates text, asks a question "
                    "about a language, or says only an isolated language name. For any unsupported "
                    "language, keep the current language and explain that only Catalan, Spanish "
                    "and English are available."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "language": {
                            "type": "string",
                            "enum": list(SUPPORTED_RESPONSE_LANGUAGES),
                            "description": (
                                "Persistent conversation language: ca for Catalan, es for Spanish "
                                "or en for English."
                            ),
                        }
                    },
                    "required": ["language"],
                    "additionalProperties": False,
                },
                handler=robot_set_language,
            ),
            ToolSpec(
                name="robot_walk",
                description=(
                    "Move the robot base a small, safe amount in one direction using "
                    "the Arduino motion controller. Use this only when the user asks "
                    "the robot to walk, move, advance, go backward or turn."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "direction": {
                            "type": "string",
                            "enum": ["forward", "backward", "left", "right"],
                            "description": "Direction of movement.",
                        },
                        "steps": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 20,
                            "description": (
                                "Small movement units for the Arduino gesture table. "
                                "Use 1 or 2 by default unless the user explicitly asks for more."
                            ),
                        },
                    },
                    "required": ["direction", "steps"],
                    "additionalProperties": False,
                },
                handler=robot_walk,
            ),
            ToolSpec(
                name="robot_greet",
                description=(
                    "Run a predefined Arduino greeting gesture. Use friendly when the user "
                    "says hello or goodbye, and wave only when the user explicitly asks "
                    "the robot to wave. Do not use formal for polite greetings; formal "
                    "now triggers the enthusiastic correct/celebration gesture."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "style": {
                            "type": "string",
                            "enum": ["friendly", "formal", "wave"],
                            "description": (
                                "Gesture style to perform: friendly is hello/goodbye, "
                                "formal is enthusiastic correct/celebration, wave is a repeated wave."
                            ),
                        }
                    },
                    "required": ["style"],
                    "additionalProperties": False,
                },
                handler=robot_greet,
            ),
            ToolSpec(
                name="robot_perform_gesture",
                description=(
                    "Run one predefined, safe conversational body gesture on the Arduino motion "
                    "controller. When a facial reaction is also appropriate, set the face first; "
                    "the gesture can start next and continue while Quibot speaks. Do not add this "
                    "tool automatically when the user has given a direct robot_move_joint command, "
                    "unless they explicitly requested a separate compatible gesture. Use it for "
                    "thinking while "
                    "reasoning, yes/no when agreeing or disagreeing, "
                    "greet_friendly for hello/goodbye, emotion for celebration, excitement "
                    "or enthusiastic correct moments, exactly for pointing, reacting to a "
                    "good question or when someone gets an answer right, sad for empathy, "
                    "and sleep before returning to rest. Prefer exactly when the robot says "
                    "Exacte, Correcte, Molt be or Bona pregunta; prefer emotion when the robot "
                    "says an excited or celebratory reply. "
                    "Context-specific gestures have priority over explain. explain is triggered "
                    "automatically while speaking only when no better context gesture was chosen; "
                    "call it manually only for a standalone talking-arm gesture. "
                    "The gesture argument is a closed enum; never invent labels or send servo angles."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "gesture": {
                            "type": "string",
                            "enum": SUPPORTED_GESTURES,
                            "description": (
                                "Exact predefined Arduino gesture. greet_friendly means greeting "
                                "or farewell; emotion means the robot says an enthusiastic "
                                "or celebratory reply; exactly means the robot says Exacte, "
                                "Correcte, Bona pregunta, points, or marks a correct answer; explain is triggered "
                                "automatically while speaking only as a fallback. Use only: "
                                f"{SUPPORTED_GESTURES_TEXT}."
                            ),
                        },
                    },
                    "required": ["gesture"],
                    "additionalProperties": False,
                },
                handler=robot_perform_gesture,
            ),
            ToolSpec(
                name="robot_move_joint",
                description=(
                    "Interpret a natural direct joint command and move one robot arm joint to a "
                    "predefined safe position. If a different facial reaction is needed, set it first. "
                    "Map 'obre la pinça' to open_gripper, 'tanca la pinça' to close_gripper, "
                    "elbow extension or flexion to extend_elbow or flex_elbow, explicit left/right "
                    "wrist turns to turn_wrist_left or turn_wrist_right, a straight or centered wrist "
                    "to center_wrist, and shoulder lowering/raising to lower_shoulder or raise_shoulder. "
                    "If direction is ambiguous, ask for clarification and do not move yet. Use one "
                    "separate call per requested movement in the user's order, and do not automatically "
                    "add robot_perform_gesture. The movement argument is a closed enum; never send degrees, "
                    "positions, speeds, serial commands or other low-level commands. A successful tool "
                    "result means the command was accepted or started, not that motion completed."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "movement": {
                            "type": "string",
                            "enum": SUPPORTED_JOINT_MOVEMENTS,
                            "description": (
                                "Exact predefined semantic joint movement. Use only: "
                                f"{SUPPORTED_JOINT_MOVEMENTS_TEXT}."
                            ),
                        },
                    },
                    "required": ["movement"],
                    "additionalProperties": False,
                },
                handler=robot_move_joint,
            ),
            ToolSpec(
                name="robot_stop",
                description=(
                    "Priority safety action for commands such as stop, para, atura't or quieto. "
                    "Execute it immediately before any face change, body gesture or spoken response. "
                    "It requests that Arduino-controlled movement stop as soon as the Arduino can "
                    "safely process STOP; do not claim the hardware has stopped before confirmation."
                ),
                parameters={
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                handler=robot_stop,
            ),
            ToolSpec(
                name="robot_set_face",
                description=(
                    "Set the robot's Dual Eye facial expression, its primary non-verbal communication "
                    "channel. When a response also needs a body gesture or joint movement, call this "
                    "before the body gesture or joint movement; the face only needs to start first. "
                    "Do not call it again when the current face already fits the intended response. "
                    "Use intensity=3 as the normal conversational value and duration_ms=null to keep "
                    "the face until it is changed; use a concrete duration mainly for blink, wink or "
                    "a brief laugh. The expression argument is a closed enum: use exactly one schema "
                    "value and never invent or translate labels. For greetings, thanks or good news use "
                    "happy; for jokes or strong amusement use laugh; for user sadness, apologies "
                    "or calm responses to insults use sad; for unclear or awkward input use "
                    "confused; for reasoning or hesitation use thinking; for unexpected news use "
                    "surprised; for quiet farewells or tiredness use sleepy or neutral. If the "
                    "emotion you want is not listed, choose the closest enum value yourself."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "expression": {
                            "type": "string",
                            "enum": SUPPORTED_EXPRESSIONS,
                            "description": (
                                "Exact Dual Eye expression enum. Use only: "
                                f"{SUPPORTED_EXPRESSIONS_TEXT}."
                            ),
                        },
                        "intensity": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 5,
                            "description": (
                                "Expression strength from 1 subtle to 5 very expressive. "
                                "Use 3 as the normal conversational default."
                            ),
                        },
                        "duration_ms": {
                            "type": ["integer", "null"],
                            "minimum": 0,
                            "description": (
                                "How long to keep the expression before returning to neutral. "
                                "Use null to keep the face until changed; use short values only "
                                "for gestures such as blink, wink or laugh."
                            ),
                        },
                    },
                    "required": ["expression", "intensity", "duration_ms"],
                    "additionalProperties": False,
                },
                handler=robot_set_face,
            ),
        ]
    )


def _is_non_critical_dual_eye_failure(result: str) -> bool:
    return result.startswith(NON_CRITICAL_DUAL_EYE_FAILURE_PREFIXES)


def _sanitize_motion_result(result: str, *, non_critical: bool = False) -> str:
    if non_critical and (_is_shoulder_position_unknown_error(result) or result.startswith(MOTION_FAILURE_PREFIXES)):
        return NON_CRITICAL_MOTION_FAILURE_MESSAGE
    if _is_shoulder_position_unknown_error(result):
        return SHOULDER_POSITION_UNKNOWN_MESSAGE
    if result.startswith(MOTION_FAILURE_PREFIXES):
        return MOTION_FAILURE_MESSAGE
    return result


def _sanitize_walk_result(result: str, *, direction: str, steps: int) -> str:
    sanitized = _sanitize_motion_result(result)
    if sanitized != result:
        return sanitized
    if result.startswith(MOTION_SUCCESS_PREFIXES):
        return _walk_success_message(direction=direction, steps=steps)
    return result


def _sanitize_joint_result(result: str, *, movement: str) -> str:
    sanitized = _sanitize_motion_result(result)
    if sanitized != result:
        return sanitized
    if result.startswith(MOTION_SUCCESS_PREFIXES):
        return _joint_success_message(movement=movement)
    return result


def _walk_success_message(*, direction: str, steps: int) -> str:
    normalized_direction = direction.strip().lower()
    confirmation_by_direction = {
        "forward": "D'acord, vaig cap endavant.",
        "backward": "D'acord, vaig enrere.",
        "left": "D'acord, giro cap a l'esquerra.",
        "right": "D'acord, giro cap a la dreta.",
    }
    confirmation = confirmation_by_direction.get(normalized_direction, "D'acord, em moc.")
    return (
        f"Physical movement command accepted: direction={normalized_direction} steps={steps}. "
        f"Reply as Quibot in first person, for example: \"{confirmation}\""
    )


def _joint_success_message(*, movement: str) -> str:
    normalized_movement = movement.strip().lower()
    confirmation_by_movement = {
        "open_gripper": "D'acord, obro la pinça.",
        "close_gripper": "D'acord, tanco la pinça.",
        "extend_elbow": "D'acord, estiro el colze.",
        "flex_elbow": "D'acord, doblego el colze.",
        "turn_wrist_left": "D'acord, giro el canell cap a l'esquerra.",
        "center_wrist": "D'acord, centro el canell.",
        "turn_wrist_right": "D'acord, giro el canell cap a la dreta.",
        "lower_shoulder": "D'acord, baixo l'espatlla.",
        "raise_shoulder": "D'acord, aixeco l'espatlla.",
    }
    confirmation = confirmation_by_movement.get(normalized_movement, "D'acord, moc l'articulació.")
    return (
        f"Physical joint command accepted: movement={normalized_movement}. "
        f"Reply as Quibot in first person, for example: \"{confirmation}\""
    )


def _is_shoulder_position_unknown_error(result: str) -> bool:
    if not result.startswith(SHOULDER_POSITION_UNKNOWN_ERROR):
        return False
    suffix = result[len(SHOULDER_POSITION_UNKNOWN_ERROR):]
    return suffix == "" or suffix.startswith(":")

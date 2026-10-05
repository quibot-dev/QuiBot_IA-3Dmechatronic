from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

try:
    import serial
except ImportError:  # pragma: no cover - exercised only when pyserial is missing
    serial = None


SUPPORTED_DIRECTIONS = ["forward", "backward", "left", "right"]
SUPPORTED_GESTURES = [
    "greet_friendly",
    "emotion",
    "wave",
    "thinking",
    "explain",
    "yes",
    "no",
    "exactly",
    "sad",
    "sleep",
]
LEGACY_GESTURE_ALIASES = {
    "greet_formal": "emotion",
    "celebrate": "exactly",
}
SUPPORTED_GESTURE_INPUTS = [*SUPPORTED_GESTURES, *LEGACY_GESTURE_ALIASES]
SUPPORTED_JOINT_MOVEMENTS = [
    "open_gripper",
    "close_gripper",
    "extend_elbow",
    "flex_elbow",
    "turn_wrist_left",
    "center_wrist",
    "turn_wrist_right",
    "lower_shoulder",
    "raise_shoulder",
]

SUPPORTED_DIRECTIONS_TEXT = ", ".join(SUPPORTED_DIRECTIONS)
SUPPORTED_GESTURES_TEXT = ", ".join(SUPPORTED_GESTURES)
SUPPORTED_JOINT_MOVEMENTS_TEXT = ", ".join(SUPPORTED_JOINT_MOVEMENTS)

SERIAL_DIRECTION_BY_NAME = {
    "forward": "FORWARD",
    "backward": "BACKWARD",
    "left": "LEFT",
    "right": "RIGHT",
}

SERIAL_GESTURE_BY_NAME = {
    "greet_friendly": "GREET_FRIENDLY",
    "emotion": "EMOTION",
    "wave": "WAVE",
    "thinking": "THINKING",
    "explain": "EXPLAIN",
    "yes": "YES",
    "no": "NO",
    "exactly": "EXACTLY",
    "sad": "SAD",
    "sleep": "SLEEP",
}

SERIAL_JOINT_COMMAND_BY_MOVEMENT = {
    "open_gripper": "JOINT:HAND:OPEN",
    "close_gripper": "JOINT:HAND:CLOSE",
    "extend_elbow": "JOINT:ELBOW:EXTEND",
    "flex_elbow": "JOINT:ELBOW:FLEX",
    "turn_wrist_left": "JOINT:WRIST:LEFT",
    "center_wrist": "JOINT:WRIST:CENTER",
    "turn_wrist_right": "JOINT:WRIST:RIGHT",
    "lower_shoulder": "JOINT:SHOULDER:LOWER",
    "raise_shoulder": "JOINT:SHOULDER:RAISE",
}


@dataclass(frozen=True)
class MoveCommand:
    direction: str
    units: int = 1

    @property
    def normalized_direction(self) -> str:
        return self.direction.strip().lower()

    def validate(self) -> None:
        if self.normalized_direction not in SUPPORTED_DIRECTIONS:
            raise ValueError(
                f"Unsupported movement direction '{self.direction}'. "
                f"Allowed values: {SUPPORTED_DIRECTIONS_TEXT}"
            )

        if not 1 <= self.units <= 20:
            raise ValueError("Movement units must be between 1 and 20")

    def as_serial_command(self) -> str:
        return f"MOVE:{SERIAL_DIRECTION_BY_NAME[self.normalized_direction]}:{self.units}"


@dataclass(frozen=True)
class GestureCommand:
    gesture: str

    @property
    def normalized_gesture(self) -> str:
        gesture = self.gesture.strip().lower()
        return LEGACY_GESTURE_ALIASES.get(gesture, gesture)

    def validate(self) -> None:
        if self.normalized_gesture not in SUPPORTED_GESTURES:
            raise ValueError(
                f"Unsupported gesture '{self.gesture}'. "
                f"Allowed values: {SUPPORTED_GESTURES_TEXT}"
            )

    def as_serial_command(self) -> str:
        return f"GESTURE:{SERIAL_GESTURE_BY_NAME[self.normalized_gesture]}"


@dataclass(frozen=True)
class JointCommand:
    movement: str

    @property
    def normalized_movement(self) -> str:
        return self.movement.strip().lower()

    def validate(self) -> None:
        if self.normalized_movement not in SUPPORTED_JOINT_MOVEMENTS:
            raise ValueError(
                f"Unsupported joint movement '{self.movement}'. "
                f"Allowed values: {SUPPORTED_JOINT_MOVEMENTS_TEXT}"
            )

    def as_serial_command(self) -> str:
        return SERIAL_JOINT_COMMAND_BY_MOVEMENT[self.normalized_movement]


class MotionClient:
    def __init__(
        self,
        *,
        serial_port: Optional[str] = None,
        serial_baudrate: int = 115200,
        timeout_s: float = 1.0,
        serial_write_timeout_s: float = 1.0,
        serial_startup_delay_s: float = 2.0,
        logger: Optional[logging.Logger] = None,
        serial_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.serial_port = serial_port
        self.serial_baudrate = serial_baudrate
        self.timeout_s = timeout_s
        self.serial_write_timeout_s = serial_write_timeout_s
        self.serial_startup_delay_s = serial_startup_delay_s
        self._logger = logger or logging.getLogger(__name__)
        self._serial_factory = serial_factory
        self._serial_connection: Any = None
        self._serial_lock = threading.Lock()

    def move(self, *, direction: str, units: int = 1) -> str:
        command = MoveCommand(direction=direction, units=units)
        command.validate()
        return self._send_or_stub(command.as_serial_command())

    def gesture(self, *, gesture: str, wait_for_response: bool = True) -> str:
        command = GestureCommand(gesture=gesture)
        command.validate()
        return self._send_or_stub(command.as_serial_command(), wait_for_response=wait_for_response)

    def move_joint(self, *, movement: str, wait_for_response: bool = True) -> str:
        command = JointCommand(movement=movement)
        command.validate()
        return self._send_or_stub(command.as_serial_command(), wait_for_response=wait_for_response)

    def stop(self) -> str:
        return self._send_or_stub("STOP")

    def warm_up(self) -> str:
        if self.serial_port is None:
            self._logger.info("Motion stub ready")
            return "Motion stub ready."

        try:
            self._get_serial_connection()
        except RuntimeError as exc:
            message = f"Motion serial unavailable: {exc}"
            self._logger.warning(message)
            return message
        except (OSError, TimeoutError) as exc:
            message = f"Motion serial write failed: {exc}"
            self._logger.warning(message)
            return message

        message = f"Motion serial ready: {self.serial_port}"
        self._logger.info(message)
        return message

    def _send_or_stub(self, command: str, *, wait_for_response: bool = True) -> str:
        if self.serial_port is None:
            self._logger.info("Motion stub command: %s", command)
            return f"Motion stub command: {command}."

        return self._send_serial(command, wait_for_response=wait_for_response)

    def _send_serial(self, command: str, *, wait_for_response: bool = True) -> str:
        try:
            response = self._write_serial_command(command, wait_for_response=wait_for_response)
        except RuntimeError as exc:
            message = f"Motion serial unavailable: {exc}"
            self._logger.warning(message)
            return message
        except (OSError, TimeoutError) as exc:
            message = f"Motion serial write failed: {exc}"
            self._logger.warning(message)
            return message

        if response is not None:
            if response.startswith("OK:"):
                message = f"Motion serial response: {response}"
                self._logger.info(message)
                return message
            if response.startswith("ERR:"):
                message = f"Motion serial error: {response}"
                self._logger.info(message)
                return message

        self._logger.info("Motion serial command sent: %s", command)
        return f"Motion serial command sent: {command}."

    def _write_serial_command(self, command: str, *, wait_for_response: bool = True) -> Optional[str]:
        command_line = f"{command}\n"
        with self._serial_lock:
            connection = self._get_serial_connection()
            self._drain_stale_serial_lines(connection)
            connection.write(command_line.encode("ascii"))
            if hasattr(connection, "flush"):
                connection.flush()
            if not wait_for_response:
                return None
            return self._read_serial_response(connection, command)

    def _drain_stale_serial_lines(self, connection: Any) -> None:
        reset_input_buffer = getattr(connection, "reset_input_buffer", None)
        if callable(reset_input_buffer):
            reset_input_buffer()
            return

        if hasattr(connection, "read") and hasattr(connection, "in_waiting"):
            waiting = int(connection.in_waiting)
            if waiting > 0:
                connection.read(waiting)
            return

        if not hasattr(connection, "readline") or not hasattr(connection, "in_waiting"):
            return

        drain_attempts = 0
        while connection.in_waiting and drain_attempts < 20:
            drain_attempts += 1
            if not connection.readline():
                return

    def _read_serial_response(self, connection: Any, command: str) -> Optional[str]:
        if not hasattr(connection, "readline"):
            return None

        expected_ok = f"OK:{command}"
        deadline = time.monotonic() + max(self.timeout_s, 0.0)
        read_attempts = 0
        has_timeout = hasattr(connection, "timeout")
        original_timeout = getattr(connection, "timeout", None) if has_timeout else None

        try:
            while read_attempts < 20:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None

                if has_timeout:
                    connection.timeout = remaining

                read_attempts += 1
                raw_line = connection.readline()
                line = self._decode_serial_line(raw_line)
                if line is None:
                    return None

                if line.startswith("ERR:"):
                    return line
                if line == expected_ok:
                    return line

            return None
        finally:
            if has_timeout:
                connection.timeout = original_timeout

    def _decode_serial_line(self, raw_line: Any) -> Optional[str]:
        if not raw_line:
            return None

        if isinstance(raw_line, bytes):
            line = raw_line.decode("ascii", errors="replace")
        else:
            line = str(raw_line)
        line = line.strip()
        return line or None

    def _open_serial(self) -> Any:
        if self._serial_factory is not None:
            return self._serial_factory(
                port=self.serial_port,
                baudrate=self.serial_baudrate,
                timeout=self.timeout_s,
                write_timeout=self.serial_write_timeout_s,
            )

        if serial is None:
            raise RuntimeError("pyserial is not installed")

        connection = serial.Serial()
        connection.port = self.serial_port
        connection.baudrate = self.serial_baudrate
        connection.timeout = self.timeout_s
        connection.write_timeout = self.serial_write_timeout_s
        connection.rtscts = False
        connection.dsrdtr = False
        _disable_serial_control_lines(connection)
        connection.open()
        return connection

    def _get_serial_connection(self) -> Any:
        if self._serial_connection is None:
            self._serial_connection = self._open_serial()
            _disable_serial_control_lines(self._serial_connection)
            if self.serial_startup_delay_s > 0:
                time.sleep(self.serial_startup_delay_s)
        return self._serial_connection

    def close(self) -> None:
        if self._serial_connection is None:
            return

        connection = self._serial_connection
        self._serial_connection = None
        _disable_serial_control_lines(connection)
        if hasattr(connection, "close"):
            connection.close()


def _disable_serial_control_lines(connection: Any) -> None:
    if hasattr(connection, "setDTR"):
        connection.setDTR(False)
    elif hasattr(connection, "dtr"):
        connection.dtr = False

    if hasattr(connection, "setRTS"):
        connection.setRTS(False)
    elif hasattr(connection, "rts"):
        connection.rts = False

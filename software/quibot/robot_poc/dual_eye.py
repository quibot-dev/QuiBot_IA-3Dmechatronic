from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib import error, request

try:
    import serial
except ImportError:  # pragma: no cover - exercised only when pyserial is missing
    serial = None


# Public expression contract exposed to the AI.
SUPPORTED_EXPRESSIONS = [
    "neutral",
    "happy",
    "sad",
    "angry",
    "surprised",
    "confused",
    "laugh",
    "thinking",
    "sleepy",
    "wink",
    "blink",
]

# Firmware commands supported by the current Dual Eye sketch. Some semantic
# expressions intentionally share the nearest available firmware animation.
SERIAL_COMMAND_BY_EXPRESSION = {
    "neutral": "neutral",
    "happy": "happy",
    "sad": "sad",
    "angry": "angry",
    "surprised": "surprised",
    "confused": "confused",
    "laugh": "laugh",
    "thinking": "confused",
    "sleepy": "sleepy",
    "wink": "blink",
    "blink": "blink",
}

SUPPORTED_EXPRESSIONS_TEXT = ", ".join(SUPPORTED_EXPRESSIONS)


@dataclass(frozen=True)
class FaceCommand:
    expression: str
    intensity: int = 3
    duration_ms: Optional[int] = None

    @property
    def normalized_expression(self) -> str:
        return self.expression.strip().lower()

    def validate(self) -> None:
        if self.normalized_expression not in SUPPORTED_EXPRESSIONS:
            raise ValueError(
                f"Unsupported Dual Eye expression '{self.expression}'. "
                f"Allowed values: {SUPPORTED_EXPRESSIONS_TEXT}"
            )

        if not 1 <= self.intensity <= 5:
            raise ValueError("Dual Eye intensity must be between 1 and 5")

        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("Dual Eye duration_ms must be zero or positive")

    def as_payload(self) -> dict:
        payload = {
            "expression": self.normalized_expression,
            "intensity": self.intensity,
        }
        if self.duration_ms is not None:
            payload["duration_ms"] = self.duration_ms
        return payload

    def as_serial_command(self) -> str:
        return SERIAL_COMMAND_BY_EXPRESSION[self.normalized_expression]


class DualEyeClient:
    def __init__(
        self,
        *,
        endpoint: Optional[str] = None,
        serial_port: Optional[str] = None,
        serial_baudrate: int = 115200,
        timeout_s: float = 1.0,
        serial_write_timeout_s: float = 1.0,
        serial_startup_delay_s: float = 2.0,
        logger: Optional[logging.Logger] = None,
        serial_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.endpoint = endpoint
        self.serial_port = serial_port
        self.serial_baudrate = serial_baudrate
        self.timeout_s = timeout_s
        self.serial_write_timeout_s = serial_write_timeout_s
        self.serial_startup_delay_s = serial_startup_delay_s
        self._logger = logger or logging.getLogger(__name__)
        self._serial_factory = serial_factory
        self._serial_reset_timer: Optional[threading.Timer] = None
        self._serial_connection: Any = None
        self._serial_lock = threading.Lock()

    def set_face(
        self,
        *,
        expression: str,
        intensity: int = 3,
        duration_ms: Optional[int] = None,
    ) -> str:
        command = FaceCommand(
            expression=expression,
            intensity=intensity,
            duration_ms=duration_ms,
        )
        command.validate()
        payload = command.as_payload()

        if self.serial_port is not None:
            return self._send_serial(command)

        if self.endpoint is None:
            self._logger.info("Dual Eye stub face command: %s", payload)
            return _format_result("Dual Eye stub face command", payload)

        return self._send_http(payload)

    def _send_http(self, payload: dict) -> str:
        body = json.dumps(payload).encode("utf-8")
        http_request = request.Request(
            self.endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(http_request, timeout=self.timeout_s) as response:
                status = response.getcode()
        except error.HTTPError as exc:
            message = f"Dual Eye HTTP error: status={exc.code}"
            self._logger.warning(message)
            return message
        except error.URLError as exc:
            message = f"Dual Eye connection failed: {exc.reason}"
            self._logger.warning(message)
            return message
        except TimeoutError:
            message = "Dual Eye connection timed out"
            self._logger.warning(message)
            return message

        return f"{_format_result('Dual Eye face command sent', payload)} status={status}"

    def _send_serial(self, command: FaceCommand) -> str:
        self._cancel_serial_reset()
        command_name = command.as_serial_command()
        try:
            self._write_serial_command(command_name)
        except RuntimeError as exc:
            message = f"Dual Eye serial unavailable: {exc}"
            self._logger.warning(message)
            return message
        except (OSError, TimeoutError) as exc:
            message = f"Dual Eye serial write failed: {exc}"
            self._logger.warning(message)
            return message

        if command.duration_ms is not None and command.duration_ms > 0 and command.normalized_expression != "neutral":
            self._schedule_serial_reset(command.duration_ms)

        self._logger.info("Dual Eye serial command sent: %s", command_name)
        result = f"Dual Eye serial command sent: {command_name}."
        if command.duration_ms is not None:
            result += f" duration_ms={command.duration_ms}."
        return result

    def _write_serial_command(self, command_name: str) -> None:
        command_line = f"{command_name}\n"
        with self._serial_lock:
            connection = self._get_serial_connection()
            connection.write(command_line.encode("ascii"))
            if hasattr(connection, "flush"):
                connection.flush()

    def _schedule_serial_reset(self, duration_ms: int) -> None:
        def reset_to_neutral() -> None:
            try:
                self._write_serial_command("neutral")
                self._logger.info("Dual Eye serial duration elapsed: neutral")
            except (OSError, TimeoutError, RuntimeError):
                return

        timer = threading.Timer(duration_ms / 1000, reset_to_neutral)
        timer.daemon = True
        self._serial_reset_timer = timer
        timer.start()

    def _cancel_serial_reset(self) -> None:
        if self._serial_reset_timer is not None:
            self._serial_reset_timer.cancel()
            self._serial_reset_timer = None

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
        self._cancel_serial_reset()
        if self._serial_connection is None:
            return

        connection = self._serial_connection
        self._serial_connection = None
        _disable_serial_control_lines(connection)
        if hasattr(connection, "close"):
            connection.close()


def _format_result(prefix: str, payload: dict) -> str:
    parts = [
        f"expression={payload['expression']}",
        f"intensity={payload['intensity']}",
    ]
    if "duration_ms" in payload:
        parts.append(f"duration_ms={payload['duration_ms']}")
    return f"{prefix}: {', '.join(parts)}."


def _disable_serial_control_lines(connection: Any) -> None:
    if hasattr(connection, "setDTR"):
        connection.setDTR(False)
    elif hasattr(connection, "dtr"):
        connection.dtr = False

    if hasattr(connection, "setRTS"):
        connection.setRTS(False)
    elif hasattr(connection, "rts"):
        connection.rts = False

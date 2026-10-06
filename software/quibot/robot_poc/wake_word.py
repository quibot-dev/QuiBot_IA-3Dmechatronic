from __future__ import annotations

import json
import logging
import queue
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import sounddevice as sd

try:
    import vosk
except ImportError:  # pragma: no cover - només s'executa si falten les dependències de Vosk
    vosk = None


StreamFactory = Callable[..., Any]
DebugCallback = Callable[[str, str], None]

QUIBOT_ALIASES = (
    "quibot",
    "qui bot",
    "ki bot",
    "kibot",
    "kiwot",
    "ki wot",
    "quimot",
    "qui mot",
    "kimot",
    "ki mot",
    "qui vot",
    "ki vot",
    "kivot",
    "quivot",
)

WAKE_WORD_CONTEXT_PREFIXES = (
    "hola",
    "ei",
    "bon dia",
    "bona tarda",
    "bona nit",
    "desperta",
    "que tal",
    "com va",
)


class WakeWordError(RuntimeError):
    """Raised when local wake word detection cannot start."""


@dataclass(frozen=True)
class WakeWordResult:
    phrase: str
    transcript: str
    source: str


class VoskWakeWordListener:
    def __init__(
        self,
        *,
        model_path: Path,
        phrases: Sequence[str],
        input_device: object = None,
        sample_rate: int = 16000,
        block_ms: int = 80,
        logger: Optional[logging.Logger] = None,
        vosk_module: Optional[Any] = None,
        stream_factory: Optional[StreamFactory] = None,
        debug_callback: Optional[DebugCallback] = None,
    ) -> None:
        self.model_path = model_path
        self.phrases = tuple(phrase.strip() for phrase in phrases if phrase.strip())
        self.input_device = input_device
        self.sample_rate = sample_rate
        self.block_ms = block_ms
        self._logger = logger or logging.getLogger(__name__)
        self._vosk = vosk_module if vosk_module is not None else vosk
        self._stream_factory = stream_factory or sd.RawInputStream
        self._debug_callback = debug_callback
        self._last_debug_text: dict[str, str] = {}
        self._model: Any = None

    def wait_for_wake_word(self) -> WakeWordResult:
        self._validate()
        recognizer = self._build_recognizer()
        audio_queue: "queue.Queue[bytes]" = queue.Queue(maxsize=50)

        def input_callback(indata: bytes, frames: int, time_info: object, status: object) -> None:
            del frames, time_info
            if status:
                self._logger.warning("Wake word input status: %s", status)
            _put_latest(audio_queue, bytes(indata))

        stream = self._stream_factory(
            samplerate=self.sample_rate,
            blocksize=self._frames_per_block,
            channels=1,
            dtype="int16",
            device=self.input_device,
            callback=input_callback,
        )

        self._logger.info("Waiting for wake word: %s", ", ".join(self.phrases))
        stream.start()
        try:
            while True:
                try:
                    frame = audio_queue.get(timeout=0.5)
                except queue.Empty:
                    continue

                result = self._process_frame(recognizer, frame)
                if result is not None:
                    return result
        finally:
            stream.stop()
            stream.close()

    @property
    def _frames_per_block(self) -> int:
        return self.sample_rate * self.block_ms // 1000

    def _validate(self) -> None:
        if self._vosk is None:
            raise WakeWordError("vosk is not installed. Install it with: python -m pip install vosk")

        if not self.phrases:
            raise WakeWordError("At least one wake word phrase is required")

        if not self.model_path.exists():
            raise WakeWordError(f"Vosk wake word model path does not exist: {self.model_path}")

    def _build_recognizer(self) -> Any:
        if hasattr(self._vosk, "SetLogLevel"):
            self._vosk.SetLogLevel(-1)

        if self._model is None:
            self._model = self._vosk.Model(str(self.model_path))

        grammar = json.dumps(build_recognition_grammar(self.phrases), ensure_ascii=False)
        return self._vosk.KaldiRecognizer(self._model, self.sample_rate, grammar)

    def _process_frame(self, recognizer: Any, frame: bytes) -> Optional[WakeWordResult]:
        if recognizer.AcceptWaveform(frame):
            transcript = _extract_recognition_text(recognizer.Result(), key="text")
            phrase = match_wake_word(transcript, self.phrases)
            if phrase is not None:
                return WakeWordResult(phrase=phrase, transcript=transcript, source="final")
            self._emit_debug("final", transcript)
            return None

        partial = _extract_recognition_text(recognizer.PartialResult(), key="partial")
        phrase = match_wake_word(partial, self.phrases)
        if phrase is None:
            self._emit_debug("partial", partial)
            return None
        return WakeWordResult(phrase=phrase, transcript=partial, source="partial")

    def _emit_debug(self, source: str, transcript: str) -> None:
        if self._debug_callback is None or not transcript:
            return

        if self._last_debug_text.get(source) == transcript:
            return

        self._last_debug_text[source] = transcript
        self._debug_callback(source, transcript)


def match_wake_word(transcript: str, phrases: Sequence[str]) -> Optional[str]:
    normalized_transcript = f" {normalize_text(transcript)} "
    for phrase in phrases:
        for alias in phrase_aliases(phrase):
            if alias and f" {alias} " in normalized_transcript:
                return phrase
    return None


def phrase_aliases(phrase: str) -> tuple[str, ...]:
    normalized_phrase = normalize_text(phrase)
    aliases = {normalized_phrase}
    if _is_quibot_trigger(normalized_phrase):
        aliases.update(QUIBOT_ALIASES)
    return tuple(sorted(alias for alias in aliases if alias))


def build_recognition_grammar(phrases: Sequence[str]) -> list[str]:
    grammar = set()
    for phrase in phrases:
        for alias in phrase_aliases(phrase):
            grammar.add(alias)
            grammar.add(f"[unk] {alias}")
            grammar.add(f"{alias} [unk]")
            grammar.add(f"[unk] {alias} [unk]")
            for prefix in WAKE_WORD_CONTEXT_PREFIXES:
                grammar.add(f"{prefix} {alias}")

    grammar.add("[unk]")
    return sorted(grammar)


def normalize_text(text: str) -> str:
    without_accents = unicodedata.normalize("NFKD", text)
    without_accents = "".join(char for char in without_accents if not unicodedata.combining(char))
    lower = without_accents.lower()
    cleaned = re.sub(r"[^a-z0-9]+", " ", lower)
    return re.sub(r"\s+", " ", cleaned).strip()


def _is_quibot_trigger(normalized_phrase: str) -> bool:
    return normalized_phrase.replace(" ", "") == "quibot"


def _extract_recognition_text(payload: str, *, key: str) -> str:
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return ""
    value = parsed.get(key, "")
    return str(value) if value is not None else ""


def _put_latest(audio_queue: "queue.Queue[bytes]", frame: bytes) -> None:
    if audio_queue.full():
        try:
            audio_queue.get_nowait()
        except queue.Empty:
            pass
    try:
        audio_queue.put_nowait(frame)
    except queue.Full:
        pass

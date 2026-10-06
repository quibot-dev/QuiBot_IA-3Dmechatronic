from __future__ import annotations

import asyncio
import logging
import queue
import sys
import threading
import time
from array import array
from typing import Optional, Union

import sounddevice as sd

AudioDevice = Union[int, str, None]

SAMPLE_RATE = 24000
CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2
CHUNK_MS = 20
FRAMES_PER_CHUNK = SAMPLE_RATE * CHUNK_MS // 1000
BYTES_PER_CHUNK = FRAMES_PER_CHUNK * CHANNELS * SAMPLE_WIDTH_BYTES


def resample_pcm16_mono(payload: bytes, source_rate: int, target_rate: int) -> bytes:
    if not payload or source_rate == target_rate:
        return payload
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("Audio sample rates must be greater than zero")
    if len(payload) % SAMPLE_WIDTH_BYTES != 0:
        raise ValueError("PCM16 payload must contain complete samples")

    samples = array("h")
    samples.frombytes(payload)
    if sys.byteorder != "little":
        samples.byteswap()

    output_count = round(len(samples) * target_rate / source_rate)
    converted = array("h")
    for output_index in range(output_count):
        source_position = output_index * source_rate / target_rate
        left_index = min(int(source_position), len(samples) - 1)
        right_index = min(left_index + 1, len(samples) - 1)
        fraction = source_position - left_index
        interpolated = round(
            samples[left_index] + (samples[right_index] - samples[left_index]) * fraction
        )
        converted.append(max(-32768, min(32767, interpolated)))

    if sys.byteorder != "little":
        converted.byteswap()
    return converted.tobytes()


def resolve_device_sample_rate(*, device: AudioDevice, direction: str) -> int:
    if direction == "input":
        check_settings = sd.check_input_settings
    elif direction == "output":
        check_settings = sd.check_output_settings
    else:
        raise ValueError("Audio direction must be 'input' or 'output'")

    try:
        check_settings(
            device=device,
            channels=CHANNELS,
            dtype="int16",
            samplerate=SAMPLE_RATE,
        )
        return SAMPLE_RATE
    except sd.PortAudioError:
        device_info = sd.query_devices(device, kind=direction)
        native_rate = round(float(device_info["default_samplerate"]))
        check_settings(
            device=device,
            channels=CHANNELS,
            dtype="int16",
            samplerate=native_rate,
        )
        return native_rate


def apply_pcm16_gain(payload: bytes, gain: float) -> bytes:
    if not payload or gain == 1.0:
        return payload
    if gain <= 0:
        raise ValueError("Audio output gain must be greater than zero")
    if len(payload) % SAMPLE_WIDTH_BYTES != 0:
        raise ValueError("PCM16 payload must contain complete samples")

    samples = array("h")
    samples.frombytes(payload)
    if sys.byteorder != "little":
        samples.byteswap()

    for index, sample in enumerate(samples):
        amplified = round(sample * gain)
        samples[index] = max(-32768, min(32767, amplified))

    if sys.byteorder != "little":
        samples.byteswap()
    return samples.tobytes()


def list_audio_devices() -> str:
    return str(sd.query_devices())


class AudioIO:
    def __init__(
        self,
        *,
        input_device: AudioDevice = None,
        output_device: AudioDevice = None,
        output_gain: float = 1.0,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._logger = logger or logging.getLogger(__name__)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._input_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
        self._output_queue: "queue.Queue[bytes]" = queue.Queue()
        self._playback_buffer = bytearray()
        self._playback_lock = threading.Lock()
        self._pending_output_bytes = 0
        self._last_output_at = 0.0
        self._input_device = input_device
        self._output_device = output_device
        self._output_gain = output_gain
        self._input_sample_rate = SAMPLE_RATE
        self._output_sample_rate = SAMPLE_RATE
        self._input_stream: Optional[sd.RawInputStream] = None
        self._output_stream: Optional[sd.RawOutputStream] = None
        self._closed = False

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._closed = False
        self._input_sample_rate = resolve_device_sample_rate(
            device=self._input_device,
            direction="input",
        )
        self._output_sample_rate = resolve_device_sample_rate(
            device=self._output_device,
            direction="output",
        )
        input_blocksize = self._input_sample_rate * CHUNK_MS // 1000
        output_blocksize = self._output_sample_rate * CHUNK_MS // 1000
        self._logger.info(
            "Audio device rates: input=%s Hz output=%s Hz API=%s Hz",
            self._input_sample_rate,
            self._output_sample_rate,
            SAMPLE_RATE,
        )
        self._input_stream = sd.RawInputStream(
            samplerate=self._input_sample_rate,
            blocksize=input_blocksize,
            channels=CHANNELS,
            dtype="int16",
            device=self._input_device,
            callback=self._input_callback,
        )
        self._output_stream = sd.RawOutputStream(
            samplerate=self._output_sample_rate,
            blocksize=output_blocksize,
            channels=CHANNELS,
            dtype="int16",
            device=self._output_device,
            callback=self._output_callback,
        )
        self._input_stream.start()
        self._output_stream.start()

    async def read_input_chunk(self) -> bytes:
        return await self._input_queue.get()

    def enqueue_output(self, chunk: bytes) -> None:
        if not chunk:
            return
        chunk = apply_pcm16_gain(chunk, self._output_gain)
        chunk = resample_pcm16_mono(chunk, SAMPLE_RATE, self._output_sample_rate)
        with self._playback_lock:
            self._pending_output_bytes += len(chunk)
            self._last_output_at = time.monotonic()
        self._output_queue.put_nowait(chunk)

    def clear_output(self) -> None:
        with self._playback_lock:
            self._playback_buffer.clear()
            self._pending_output_bytes = 0
        while True:
            try:
                self._output_queue.get_nowait()
            except queue.Empty:
                break

    def is_output_active(self, hold_ms: int = 0) -> bool:
        with self._playback_lock:
            pending = self._pending_output_bytes
            last_output_at = self._last_output_at

        if pending > 0:
            return True

        if hold_ms <= 0:
            return False

        return (time.monotonic() - last_output_at) < (hold_ms / 1000)

    def stop(self) -> None:
        self._closed = True
        self.clear_output()
        if self._input_stream is not None:
            self._input_stream.stop()
            self._input_stream.close()
            self._input_stream = None
        if self._output_stream is not None:
            self._output_stream.stop()
            self._output_stream.close()
            self._output_stream = None

    def _input_callback(self, indata: bytes, frames: int, time_info: object, status: sd.CallbackFlags) -> None:
        del frames, time_info
        if status:
            self._logger.warning("Input audio status: %s", status)

        if self._closed or self._loop is None:
            return

        chunk = resample_pcm16_mono(bytes(indata), self._input_sample_rate, SAMPLE_RATE)

        def push() -> None:
            if self._input_queue.full():
                try:
                    self._input_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                self._input_queue.put_nowait(chunk)
            except asyncio.QueueFull:
                pass

        self._loop.call_soon_threadsafe(push)

    def _output_callback(self, outdata: bytearray, frames: int, time_info: object, status: sd.CallbackFlags) -> None:
        del frames, time_info
        if status:
            self._logger.warning("Output audio status: %s", status)

        with self._playback_lock:
            required = len(outdata)
            while len(self._playback_buffer) < required:
                try:
                    self._playback_buffer.extend(self._output_queue.get_nowait())
                except queue.Empty:
                    break

            available = len(self._playback_buffer)
            consume = min(available, required)
            if consume > 0:
                payload = bytes(self._playback_buffer[:consume])
                del self._playback_buffer[:consume]
                self._pending_output_bytes = max(0, self._pending_output_bytes - consume)
                self._last_output_at = time.monotonic()
            else:
                payload = b""

        if len(payload) < required:
            payload += b"\x00" * (required - len(payload))

        outdata[:] = payload

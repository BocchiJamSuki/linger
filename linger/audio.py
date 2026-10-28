"""System-audio capture via WASAPI loopback: records what a speaker plays, never a microphone."""
from dataclasses import dataclass
import queue
import threading
import time

import numpy as np

from .errors import UserError

RATE = 48000
CHUNK_MS = 100
FOLLOW_DEFAULT = ''  # Source uid meaning "whatever Windows' default output is when we start".


@dataclass(frozen=True)
class Source:
    uid: str
    name: str
    rate: int = RATE
    channels: int = 2


def list_sources():
    """The default-output entry first, then every speaker that offers loopback."""
    import soundcard as sc
    # Virtual cables (VB-Audio and the like) only carry audio someone routed there on purpose.
    loopbacks = [m for m in sc.all_microphones(include_loopback=True)
                 if m.isloopback and not any(v in m.name for v in ('VB-Audio', 'Virtual Cable', 'VoiceMeeter'))]
    try:
        default = sc.default_speaker().name
    except Exception:
        default = None
    sources = [Source(FOLLOW_DEFAULT, f'系统默认 · {default}' if default else '系统默认输出')]
    sources += [Source(m.id, m.name, RATE, max(1, min(2, m.channels))) for m in sorted(loopbacks, key=lambda m: m.name)]
    return sources


def mono_pcm(data):
    """Float frames (n, channels) → mono little-endian PCM16 bytes, clipped and NaN-safe."""
    data = np.asarray(data, dtype='float32')
    mono = data.mean(axis=1) if data.ndim == 2 else data
    mono = np.nan_to_num(mono, nan=0.0, posinf=1.0, neginf=-1.0)
    return (np.clip(mono, -1, 32767 / 32768) * 32768).astype('<i2').tobytes()


def peak_of(pcm):
    if not pcm:
        return 0.0
    return float(np.max(np.abs(np.frombuffer(pcm, dtype='<i2').astype(np.int32)))) / 32768


class Packetizer:
    """Re-slices arbitrary PCM into exact CHUNK_MS packets."""

    def __init__(self, rate):
        self.size = rate * CHUNK_MS // 1000 * 2
        self.pending = bytearray()

    def feed(self, pcm):
        self.pending.extend(pcm)
        chunks = []
        while len(self.pending) >= self.size:
            chunks.append(bytes(self.pending[:self.size]))
            del self.pending[:self.size]
        return chunks


class LoopbackCapture:
    """Blocking WASAPI loopback recorder isolated in a producer thread with a bounded queue."""

    STALL_SECONDS = 3
    MAX_AGE = 0.5

    def __init__(self, source, stopped):
        self.source = source
        self.stopped = stopped
        self.closed = threading.Event()  # Per-capture: reconnects close one and open another.
        self.blocks = queue.Queue(maxsize=50)
        self.recorder = None
        self.thread = None
        self.fault = None
        self.peak = 0.0
        self.last_block = time.monotonic()

    def open(self):
        import soundcard as sc
        try:
            uid = self.source.uid or sc.default_speaker().id
            matches = [m for m in sc.all_microphones(include_loopback=True) if m.isloopback and m.id == uid]
        except Exception:
            matches = []
        if len(matches) != 1:
            raise UserError('找不到所选的扬声器。请检查设备，或改为「跟随系统默认」。')
        self.recorder = matches[0].recorder(samplerate=self.source.rate, channels=self.source.channels,
                                            blocksize=self.source.rate // 20)
        self.recorder.__enter__()

    def start(self):
        if self.stopped.is_set():
            return
        self.last_block = time.monotonic()
        self.thread = threading.Thread(target=self._record, name='wasapi-loopback', daemon=True)
        self.thread.start()

    def _record(self):
        frames = self.source.rate * CHUNK_MS // 1000
        try:
            while not (self.stopped.is_set() or self.closed.is_set()):
                data = self.recorder.record(frames)
                if self.stopped.is_set() or self.closed.is_set():
                    return
                self.blocks.put_nowait((time.monotonic(), data))
                self.last_block = time.monotonic()
        except queue.Full:
            self.fault = '本地音频积压，已重新同步。'
        except Exception:
            if not (self.stopped.is_set() or self.closed.is_set()):
                self.fault = '扬声器回环采集中断。'

    def take(self):
        """Next mono PCM block, or None if nothing is ready. Raises on stalls and backlog."""
        if self.stopped.is_set():
            return None
        if self.fault:
            raise UserError(self.fault)
        if time.monotonic() - self.last_block > self.STALL_SECONDS:
            raise UserError('扬声器超过 3 秒没有提供音频。')
        try:
            created, data = self.blocks.get_nowait()
        except queue.Empty:
            return None
        if time.monotonic() - created > self.MAX_AGE:
            raise UserError('音频积压超过 500 ms，已重新同步。')
        pcm = mono_pcm(data)
        self.peak = peak_of(pcm)
        return pcm

    def close(self):
        self.closed.set()
        if self.thread is not None:
            self.thread.join(timeout=0.5)
            self.thread = None
        if self.recorder is not None:
            try:
                self.recorder.__exit__(None, None, None)
            except Exception:
                pass
            self.recorder = None
        while True:
            try:
                self.blocks.get_nowait()
            except queue.Empty:
                break

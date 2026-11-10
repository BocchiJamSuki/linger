"""System-audio capture via WASAPI loopback: records what an output device plays, never a microphone."""
from collections import deque
from dataclasses import dataclass, replace
import threading
import time

import numpy as np

from .errors import UserError
from .i18n import tr

# Speech models work at 16 kHz; Windows resamples for us, and the upload is a third of 48 kHz.
RATE = 16000
CHUNK_MS = 100
CHUNK_SECONDS = CHUNK_MS / 1000
FOLLOW_DEFAULT = ''  # Source uid meaning "whatever Windows' default output currently is".
VIRTUAL_DEVICES = ('VB-Audio', 'Virtual Cable', 'VoiceMeeter')


@dataclass(frozen=True)
class Source:
    uid: str
    name: str
    channels: int = 2


def default_output():
    """(uid, name) of the device Windows currently plays to."""
    import soundcard as sc
    speaker = sc.default_speaker()
    return speaker.id, speaker.name


def list_sources():
    """The default-output entry first, then every output device that offers loopback."""
    import soundcard as sc
    # Virtual cables only carry audio that someone routed there on purpose.
    loopbacks = [m for m in sc.all_microphones(include_loopback=True)
                 if m.isloopback and not any(v in m.name for v in VIRTUAL_DEVICES)]
    try:
        label = tr('source.default', name=default_output()[1])
    except Exception:
        label = tr('source.default_plain')
    sources = [Source(FOLLOW_DEFAULT, label)]
    sources += [Source(m.id, m.name, max(1, min(2, m.channels))) for m in sorted(loopbacks, key=lambda m: m.name)]
    return sources


def to_mono(data):
    """Float frames (n, channels) → mono float32, NaN-safe."""
    data = np.asarray(data, dtype='float32')
    mono = data.mean(axis=1) if data.ndim == 2 else data
    return np.nan_to_num(mono, nan=0.0, posinf=1.0, neginf=-1.0)


def quantize(mono):
    """Mono float → little-endian PCM16 bytes, clipped."""
    return (np.clip(mono, -1, 32767 / 32768) * 32768).astype('<i2').tobytes()


def mono_pcm(data):
    return quantize(to_mono(data))


class Leveler:
    """Boost-only automatic gain, applied in float before quantising to 16 bits.

    Loopback follows the system volume, so a quiet volume setting would otherwise reach the
    recogniser as a faint signal. Digital silence stays silence; loud audio is left alone.
    """

    TARGET = 0.25
    MAX_GAIN = 30.0
    RELEASE = 0.97   # Envelope decay per 100 ms block: gain recovers over a few seconds.
    FLOOR = 1e-4     # Below this the block is treated as silence and not amplified further.

    def __init__(self):
        self.envelope = 0.0
        self.gain = 1.0

    def process(self, mono):
        if not len(mono):
            return mono
        peak = float(np.max(np.abs(mono)))
        self.envelope = max(peak, self.envelope * self.RELEASE)
        if self.envelope < self.FLOOR:
            return mono
        target = min(self.MAX_GAIN, max(1.0, self.TARGET / self.envelope))
        if target <= self.gain:
            self.gain = target                      # Louder input: drop the gain at once, never clip.
            return mono * target
        ramp = np.linspace(self.gain, target, len(mono), dtype='float32')  # Quieter: rise smoothly.
        self.gain = target
        return mono * ramp


def peak_of(pcm):
    if not pcm:
        return 0.0
    return float(np.max(np.abs(np.frombuffer(pcm, dtype='<i2').astype(np.int32)))) / 32768


class Packetizer:
    """Re-slices arbitrary PCM into exact CHUNK_MS packets."""

    def __init__(self, rate=RATE):
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
    """One open device: a blocking WASAPI loopback recorder in its own producer thread."""

    STALL_SECONDS = 3

    def __init__(self, source, stopped):
        self.source = source
        self.stopped = stopped
        self.closed = threading.Event()
        self.blocks = deque(maxlen=300)  # 30 s; the consumer normally drains within milliseconds.
        self.recorder = None
        self.thread = None
        self.fault = None
        self.leveler = Leveler()
        self.last_block = time.monotonic()

    def open(self):
        import soundcard as sc
        try:
            matches = [m for m in sc.all_microphones(include_loopback=True)
                       if m.isloopback and m.id == self.source.uid]
        except Exception:
            matches = []
        if len(matches) != 1:
            raise UserError(tr('err.device_missing'))
        self.recorder = matches[0].recorder(samplerate=RATE, channels=self.source.channels, blocksize=RATE // 20)
        self.recorder.__enter__()

    def start(self):
        self.last_block = time.monotonic()
        self.thread = threading.Thread(target=self._record, name='wasapi-loopback', daemon=True)
        self.thread.start()

    def _halted(self):
        return self.stopped.is_set() or self.closed.is_set()

    def _record(self):
        frames = RATE * CHUNK_MS // 1000
        try:
            while not self._halted():
                data = self.recorder.record(frames)
                if self._halted():
                    return
                self.blocks.append(data)
                self.last_block = time.monotonic()
        except Exception:
            if not self._halted():
                self.fault = tr('err.capture')

    def take(self):
        """Next mono PCM block, or None when nothing is ready. Raises UserError if capture broke."""
        if self.fault:
            raise UserError(self.fault)
        if time.monotonic() - self.last_block > self.STALL_SECONDS:
            raise UserError(tr('err.stall'))
        try:
            return quantize(self.leveler.process(to_mono(self.blocks.popleft())))
        except IndexError:
            return None

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
        self.blocks.clear()


class AudioFeed:
    """Session-long audio supply, independent of the network connection.

    Follows the default output device when it changes, reopens capture after a fault, and
    keeps track of silence. `poll()` never raises: problems are reported through `problem`.
    """

    FOLLOW_EVERY = 2.0
    REOPEN_EVERY = 1.0

    def __init__(self, source, stopped, capture_factory=LoopbackCapture, default_output=default_output):
        self.source = source
        self.stopped = stopped
        self.capture_factory = capture_factory
        self.default_output = default_output
        self.capture = None
        self.bound = None           # uid of the device actually open.
        self.device_name = ''
        self.packetizer = Packetizer()
        self.peak = 0.0
        self.heard = False          # Any non-silent audio since the feed started.
        self.silent_chunks = 0
        self.problem = None         # Why capture is down, while it is.
        self.switched = None        # Device name after following a change; the engine consumes it.
        self._next_open = self._next_follow = 0.0

    @property
    def silent_seconds(self):
        return self.silent_chunks * CHUNK_SECONDS

    def poll(self):
        """New 100 ms PCM chunks since the last call."""
        now = time.monotonic()
        if self.capture is None:
            if now >= self._next_open:
                self._open(now)
        elif self.source.uid == FOLLOW_DEFAULT and now >= self._next_follow:
            self._next_follow = now + self.FOLLOW_EVERY
            try:
                current = self.default_output()[0]
            except Exception:
                current = self.bound
            if current != self.bound:
                self._close()
                self._open(now, switched=True)
        chunks = []
        if self.capture is not None:
            try:
                while (pcm := self.capture.take()) is not None:
                    chunks.extend(self.packetizer.feed(pcm))
            except UserError as exc:
                self.problem = str(exc)
                self._close()
                self._next_open = now + self.REOPEN_EVERY
        self.peak = 0.0
        for chunk in chunks:
            peak = peak_of(chunk)
            self.peak = max(self.peak, peak)
            if peak > 0:
                self.heard = True
                self.silent_chunks = 0
            else:
                self.silent_chunks += 1
        return chunks

    def _open(self, now, switched=False):
        capture = None
        try:
            uid, name = (self.source.uid, self.source.name) if self.source.uid else self.default_output()
            capture = self.capture_factory(replace(self.source, uid=uid), self.stopped)
            capture.open()
            capture.start()
        except Exception as exc:
            if capture is not None:
                capture.close()
            self.problem = str(exc) if isinstance(exc, UserError) else tr('err.device_missing')
            self._next_open = now + self.REOPEN_EVERY
            return
        self.capture, self.bound, self.device_name = capture, uid, name
        self.problem = None
        self.packetizer = Packetizer()
        self._next_follow = now + self.FOLLOW_EVERY
        if switched:
            self.switched = name

    def _close(self):
        if self.capture is not None:
            self.capture.close()
            self.capture = None

    def close(self):
        self._close()

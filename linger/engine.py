"""The worker thread: owns capture and the socket, reconnects on its own, reports via a mailbox."""
import asyncio
from collections import deque
import json
import logging
import threading
import time
from typing import NamedTuple

from websockets.asyncio.client import connect

from .audio import LoopbackCapture, Packetizer
from .errors import UserError
from .scribe import SESSION_LIMIT, ServerEvent, api_key, audio_message, connection_url, decode_event

# Status tones drive the UI's colour language.
IDLE, BUSY, LIVE, OK, WARN, ERROR = 'idle', 'busy', 'live', 'ok', 'warn', 'error'

# Transcript events, in arrival order.
COMMIT, PROVISIONAL, GAP = 'commit', 'provisional', 'gap'

RETRY_DELAYS = (0.3, 1, 2, 3, 5)
MAX_FAILED_ATTEMPTS = 8  # Consecutive attempts that never reached a live session.


class Snapshot(NamedTuple):
    events: list
    partial: str | None
    status: str | None
    tone: str | None
    level: float


class Mailbox:
    """Thread-safe hand-off. Commits/gaps keep order; partials coalesce to the latest."""

    def __init__(self):
        self.lock = threading.Lock()
        self.events = deque()
        self.partial = None   # Latest partial not yet drained.
        self.speaking = ''    # Current uncommitted text, survives drains.
        self.status = self.tone = None
        self.level = 0.0

    def transcript(self, kind, text):
        with self.lock:
            if kind == 'committed_transcript':
                if text.strip():
                    self.events.append((COMMIT, text))
                self.partial = self.speaking = ''
            elif kind == 'partial_transcript':
                self.partial = self.speaking = text

    def interrupt(self):
        """The connection dropped mid-sentence: keep the words, marked as unconfirmed."""
        with self.lock:
            if self.speaking.strip():
                self.events.append((PROVISIONAL, self.speaking))
            self.partial = self.speaking = ''

    def gap(self, seconds):
        with self.lock:
            self.events.append((GAP, seconds))

    def state(self, text, tone=IDLE):
        with self.lock:
            self.status, self.tone = text, tone

    def meter(self, peak):
        with self.lock:
            self.level = peak

    def drain(self):
        with self.lock:
            snap = Snapshot(list(self.events), self.partial, self.status, self.tone, self.level)
            self.events.clear()
            self.partial = self.status = self.tone = None
            return snap


class Engine(threading.Thread):
    """mode 'live' streams until stopped; mode 'selftest' checks audio and the API once."""

    def __init__(self, source, mailbox, *, mode='live', connector=connect, capture_factory=LoopbackCapture,
                 retry_delays=RETRY_DELAYS):
        super().__init__(name='scribe-engine', daemon=False)
        self.source, self.mailbox, self.mode = source, mailbox, mode
        self.connector = connector
        self.capture_factory = capture_factory
        self.retry_delays = retry_delays
        self.stopped = threading.Event()
        self.loop = self.task = None
        self.sent_chunks = 0
        self.connections = 0

    def stop(self):
        if self.stopped.is_set():
            return
        self.stopped.set()  # Capture stops accepting audio immediately.
        if self.loop and self.task:
            try:
                self.loop.call_soon_threadsafe(self.task.cancel)
            except RuntimeError:
                pass  # Loop already closed.

    def run(self):
        try:
            asyncio.run(self._main())
        except Exception:
            self.mailbox.state('后台任务异常，资源已释放。请重新开始。', ERROR)

    async def _main(self):
        self.loop = asyncio.get_running_loop()
        self.task = asyncio.current_task()
        key = ''
        try:
            if self.stopped.is_set():
                self.mailbox.state('已停止。')
                return
            key = api_key()
            if self.mode == 'selftest':
                await self._selftest(key)
            else:
                await self._live(key)
        except asyncio.CancelledError:
            self.mailbox.state('自检已取消。' if self.mode == 'selftest' else '已停止。')
        except UserError as exc:
            message = str(exc).replace(key, '[redacted]') if key else str(exc)
            self.mailbox.state(message if len(message) < 240 else '会话失败，请检查设备和网络。', ERROR)
        except Exception as exc:
            self.mailbox.state(f'意外错误（{type(exc).__name__}），资源已释放。', ERROR)
        finally:
            self.stopped.set()
            self.task = None

    # ── live streaming with reconnection ──────────────────────────────────────
    async def _live(self, key):
        failures = 0
        dropped_at = None
        while not self.stopped.is_set():
            attempt = {'live': False}
            try:
                await self._stream(key, attempt, dropped_at)
                return  # Only returns normally when stopped.
            except UserError as exc:
                if exc.fatal:
                    raise
                quick = isinstance(exc, ServerEvent) and exc.kind == SESSION_LIMIT
                reason = str(exc)
            except (OSError, TimeoutError, asyncio.TimeoutError, json.JSONDecodeError) as exc:
                quick, reason = False, f'网络中断（{type(exc).__name__}）。'
            except Exception as exc:  # websockets.ConnectionClosed and friends.
                quick, reason = False, f'连接中断（{type(exc).__name__}）。'
            if self.stopped.is_set():
                return
            self.mailbox.interrupt()
            self.mailbox.meter(0.0)
            if dropped_at is None:
                dropped_at = time.monotonic()
            failures = 0 if attempt['live'] else failures + 1
            if failures >= MAX_FAILED_ATTEMPTS:
                raise UserError(f'连续 {failures} 次重连失败，已停止。{reason}')
            delay = 0 if quick else self.retry_delays[min(failures, len(self.retry_delays) - 1)]
            self.mailbox.state(f'{reason}正在自动重连…', WARN)
            await asyncio.sleep(delay)

    async def _stream(self, key, attempt, dropped_at):
        capture = self.capture_factory(self.source, self.stopped)
        tasks = []
        try:
            self.mailbox.state('正在打开音频…' if dropped_at is None else '正在重连…', BUSY)
            capture.open()
            if self.stopped.is_set():
                return
            if dropped_at is None:
                self.mailbox.state('正在连接 ElevenLabs…', BUSY)
            async with self.connector(connection_url(self.source.rate), additional_headers={'xi-api-key': key},
                                      open_timeout=8, close_timeout=1, ping_interval=10, ping_timeout=8,
                                      max_size=2**20, max_queue=16, logger=_silent_logger()) as ws:
                self.connections += 1
                try:
                    ready = asyncio.Event()
                    tasks.append(asyncio.create_task(self._receive(ws, ready)))
                    tasks.append(asyncio.create_task(self._send(ws, capture, ready, attempt, dropped_at)))
                    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        task.result()
                    if not self.stopped.is_set():
                        raise UserError('连接被服务器关闭。')
                finally:
                    await self._halt(tasks, capture)  # Stop sender/receiver before the close handshake.
        finally:
            await self._halt(tasks, capture)  # Also covers failures before the socket opened.

    async def _halt(self, tasks, capture):
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        tasks.clear()
        capture.close()

    async def _receive(self, ws, ready):
        async for raw in ws:
            if self.stopped.is_set():
                return
            kind, text = decode_event(json.loads(raw))
            if kind == 'session_started':
                ready.set()
            elif kind in ('partial_transcript', 'committed_transcript'):
                self.mailbox.transcript(kind, text)

    async def _send(self, ws, capture, ready, attempt, dropped_at):
        await asyncio.wait_for(ready.wait(), timeout=8)
        if self.stopped.is_set():
            return
        capture.start()
        attempt['live'] = True
        if dropped_at is not None:
            self.mailbox.gap(time.monotonic() - dropped_at)
        self.mailbox.state('正在实时转写', LIVE)
        packetizer = Packetizer(self.source.rate)
        while not self.stopped.is_set():
            pcm = capture.take()
            if pcm is None:
                await asyncio.sleep(0.005)
                continue
            for chunk in packetizer.feed(pcm):
                if self.stopped.is_set():
                    return
                await asyncio.wait_for(ws.send(json.dumps(audio_message(chunk, self.source.rate))), timeout=1)
                self.sent_chunks += 1
            self.mailbox.meter(capture.peak)

    # ── pre-interview self test ───────────────────────────────────────────────
    async def _selftest(self, key, seconds=3):
        capture = self.capture_factory(self.source, self.stopped)
        peak = 0.0
        frames = 0
        try:
            self.mailbox.state(f'自检 1/2：监听扬声器 {seconds} 秒…', BUSY)
            capture.open()
            capture.start()
            deadline = time.monotonic() + seconds
            while not self.stopped.is_set() and time.monotonic() < deadline:
                pcm = capture.take()
                if pcm:
                    frames += 1
                    peak = max(peak, capture.peak)
                    self.mailbox.meter(capture.peak)
                await asyncio.sleep(0.01)
        finally:
            capture.close()
        self.mailbox.meter(0.0)
        if not frames:
            raise UserError('自检失败：扬声器没有提供任何音频数据。')
        audio = '听到了声音' if peak >= 0.001 else '没有声音（此刻没在播放的话属于正常）'
        self.mailbox.state('自检 2/2：连接 ElevenLabs…', BUSY)
        async with self.connector(connection_url(self.source.rate), additional_headers={'xi-api-key': key},
                                  open_timeout=8, close_timeout=1, logger=_silent_logger()) as ws:
            started = False
            async with asyncio.timeout(8):
                async for raw in ws:
                    if decode_event(json.loads(raw))[0] == 'session_started':
                        started = True
                        break
            if not started:
                raise UserError('自检失败：服务器没有确认会话。')
        self.mailbox.state(f'自检通过 · 音频：{audio} · API：密钥有效，连接正常', OK)


def _silent_logger():
    logger = logging.Logger('scribe-private-socket')  # Never log headers (they carry the key).
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger

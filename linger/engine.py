"""The worker thread: owns audio and the socket, reconnects on its own, reports via a mailbox.

Audio and network are decoupled. A pump collects audio for the whole session into `unsent`;
the sender drains it. While the link is down the audio simply waits, and after a reconnect
the new session first receives everything the old one heard but never confirmed, so a drop
in the middle of a sentence normally costs nothing.
"""
import asyncio
from collections import deque
import json
import logging
import threading
import time
from typing import NamedTuple

from websockets.asyncio.client import connect

from .audio import CHUNK_SECONDS, RATE, AudioFeed, LoopbackCapture, default_output
from .errors import UserError
from .i18n import tr
from .scribe import SESSION_LIMIT, TIMED_COMMIT, ServerEvent, api_key, audio_message, committed_until
from .scribe import connection_url, decode_event

# Status tones drive the UI's colour language. QUIET: connected, but no audio is arriving.
IDLE, BUSY, LIVE, OK, WARN, ERROR, QUIET = 'idle', 'busy', 'live', 'ok', 'warn', 'error', 'quiet'

# Transcript events, in arrival order.
COMMIT, PROVISIONAL, GAP = 'commit', 'provisional', 'gap'

RETRY_DELAYS = (0.3, 1, 2, 3, 5)
BACKLOG_CHUNKS = 600       # 60 s of audio may wait for the network; beyond that the oldest is lost.
UNCONFIRMED_CHUNKS = 300   # Audio kept for replay: sent, but its words are not committed yet.
PLAIN_COMMIT_GRACE = 1.0   # How long to wait for a commit's timed twin before using the plain one.
REPLAY_SPEED = 8.0         # Catch-up pace as a multiple of real time. Measured against the service:
                           # 16x is accepted, unthrottled sending is cut off with queue_overflow.
NEVER_HEARD_AFTER = 10     # Seconds of live silence from the start before pointing at the routing.
QUIET_AFTER = 5
STALE_PARTIAL_AFTER = 8    # If a reconnect brings no new words, keep the old ones as unconfirmed.
SEND_TIMEOUT = 2           # A send that blocks this long means the link is dead.


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
        """Words in progress will never be confirmed: keep them, marked as such."""
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

    def __init__(self, source, mailbox, *, mode='live', language='en', connector=connect,
                 capture_factory=LoopbackCapture, default_output=default_output,
                 retry_delays=RETRY_DELAYS, replay_speed=REPLAY_SPEED):
        super().__init__(name='scribe-engine', daemon=False)
        self.source, self.mailbox, self.mode, self.language = source, mailbox, mode, language
        self.connector = connector
        self.capture_factory = capture_factory
        self.default_output = default_output
        self.retry_delays = retry_delays
        self.replay_speed = replay_speed
        self.stopped = threading.Event()
        self.loop = self.task = self.pump = None
        self.unsent = deque()        # Audio waiting for the network.
        self.unconfirmed = deque()   # Audio sent since the last commit, replayed after a drop.
        self.lost = 0                # Chunks dropped because the backlog overflowed.
        self.link = (BUSY, '')       # Connection state as last set by the connection loop.
        self.note = ('', 0.0)        # Short-lived remark appended to the live status.
        self.reported = None
        self.live_since = None
        self.session_sent = 0        # Chunks sent on the current connection (its audio clock).
        self.plain_commit = None     # (text, timer) while waiting for the timed twin.
        self.fresh = False           # Any transcript event on the current connection.
        self.sent_chunks = 0
        self.connections = 0

    def stop(self):
        if self.stopped.is_set():
            return
        self.stopped.set()
        if self.loop and self.task:
            try:
                self.loop.call_soon_threadsafe(self.task.cancel)
            except RuntimeError:
                pass  # Loop already closed.

    def run(self):
        try:
            asyncio.run(self._main())
        except Exception:
            self.mailbox.state(tr('err.background'), ERROR)

    async def _main(self):
        self.loop = asyncio.get_running_loop()
        self.loop.set_exception_handler(_quiet_socket_teardown)
        self.task = asyncio.current_task()
        key = ''
        try:
            if self.stopped.is_set():
                self.mailbox.state(tr('status.stopped'))
                return
            key = api_key()
            if self.mode == 'selftest':
                await self._selftest(key)
            else:
                await self._live(key)
        except asyncio.CancelledError:
            self.mailbox.state(tr('test.cancelled') if self.mode == 'selftest' else tr('status.stopped'))
        except UserError as exc:
            message = str(exc).replace(key, '[redacted]') if key else str(exc)
            self.mailbox.state(message if len(message) < 300 else tr('err.session'), ERROR)
        except Exception as exc:
            self.mailbox.state(tr('err.unexpected', name=type(exc).__name__), ERROR)
        finally:
            self.stopped.set()
            self.task = None

    def _feed(self):
        return AudioFeed(self.source, self.stopped, self.capture_factory, self.default_output)

    # ── live streaming ────────────────────────────────────────────────────────
    async def _live(self, key):
        feed = self._feed()
        self.pump = asyncio.create_task(self._pump(feed))
        failures = 0
        reconnecting = False
        try:
            while not self.stopped.is_set():
                attempt = {'live': False}
                try:
                    await self._stream(key, attempt, reconnecting)
                    return  # Only returns normally when stopped.
                except UserError as exc:
                    if exc.fatal:
                        raise
                    quick = isinstance(exc, ServerEvent) and exc.kind == SESSION_LIMIT
                    reason = str(exc)
                    if isinstance(exc, ServerEvent) and exc.kind == 'queue_overflow':
                        self.replay_speed = max(2.0, (self.replay_speed or 2 * REPLAY_SPEED) / 2)  # Sent too fast.
                except (OSError, TimeoutError, asyncio.TimeoutError, json.JSONDecodeError) as exc:
                    quick, reason = False, tr('err.network', name=type(exc).__name__)
                except Exception as exc:  # websockets.ConnectionClosed and friends.
                    quick, reason = False, tr('err.link', name=type(exc).__name__)
                if self.stopped.is_set():
                    return
                self._requeue()
                reconnecting = True
                failures = 0 if attempt['live'] else failures + 1
                # Never give up: a network that returns after minutes should simply resume.
                delay = 0 if quick else self.retry_delays[min(failures, len(self.retry_delays) - 1)]
                self.link = (WARN, tr('status.retry', reason=reason, n=failures + 1))
                await asyncio.sleep(delay)
        finally:
            self.pump.cancel()
            await asyncio.gather(self.pump, return_exceptions=True)
            feed.close()

    def _requeue(self):
        """Put audio whose words were never committed back at the front of the queue."""
        self.unsent.extendleft(reversed(self.unconfirmed))
        self.unconfirmed.clear()
        self._trim_backlog()

    def _trim_backlog(self):
        while len(self.unsent) > BACKLOG_CHUNKS:
            self.unsent.popleft()
            self.lost += 1

    async def _pump(self, feed):
        """Runs for the whole session, connected or not."""
        while not self.stopped.is_set():
            self.unsent.extend(feed.poll())
            self._trim_backlog()
            self.mailbox.meter(feed.peak)
            if feed.switched:
                self.note = (tr('status.switched', name=feed.switched), time.monotonic() + 6)
                feed.switched = None
            self._report(feed)
            await asyncio.sleep(0.02)

    def _report(self, feed):
        now = time.monotonic()
        tone, text = self.link
        if tone == LIVE:
            if feed.problem:
                tone, text = QUIET, tr('status.device_down', reason=feed.problem)
            elif not feed.heard and now - self.live_since > NEVER_HEARD_AFTER:
                tone, text = QUIET, tr('status.never_heard')
            elif feed.silent_seconds > QUIET_AFTER:
                text = tr('status.live_quiet')
            else:
                text = tr('status.live')
            note, until = self.note
            if note and now < until:
                text = f'{text} · {note}'
        if (tone, text) != self.reported:
            self.reported = (tone, text)
            self.mailbox.state(text, tone)

    async def _stream(self, key, attempt, reconnecting):
        if not reconnecting:
            self.link = (BUSY, tr('status.connecting'))
        tasks = []
        # Pings find a silently dead link within about five seconds.
        async with self.connector(connection_url(RATE, self.language), additional_headers={'xi-api-key': key},
                                  open_timeout=8, close_timeout=1, ping_interval=2, ping_timeout=3,
                                  max_size=2**20, max_queue=16, logger=_silent_logger()) as ws:
            self.connections += 1
            try:
                ready = asyncio.Event()
                tasks.append(asyncio.create_task(self._receive(ws, ready, key)))
                tasks.append(asyncio.create_task(self._send(ws, ready, attempt, reconnecting)))
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
                if not self.stopped.is_set():
                    raise UserError(tr('err.closed'))
            finally:
                for task in tasks:  # Stop sender/receiver before the close handshake.
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _receive(self, ws, ready, key):
        try:
            async for raw in ws:
                if self.stopped.is_set():
                    return
                event = json.loads(raw)
                kind, text = decode_event(event, key)
                if kind == 'session_started':
                    ready.set()
                elif kind == 'partial_transcript':
                    self.fresh = True
                    self.mailbox.transcript(kind, text)
                elif kind == TIMED_COMMIT:
                    # Text and its exact audio extent arrive together: show the words and stop
                    # keeping their audio for replay in one step, so a drop can neither lose
                    # nor repeat them.
                    self._drop_plain_commit()
                    self.fresh = True
                    self.mailbox.transcript('committed_transcript', text)
                    self._confirm_until(committed_until(event))
                elif kind == 'committed_transcript':
                    # The same commit without timings arrives first. Use it only if its timed
                    # twin never shows up.
                    self._drop_plain_commit()
                    timer = self.loop.call_later(PLAIN_COMMIT_GRACE, self._use_plain_commit)
                    self.plain_commit = (text, timer)
        finally:
            self._drop_plain_commit()

    def _drop_plain_commit(self):
        if self.plain_commit:
            self.plain_commit[1].cancel()
            self.plain_commit = None

    def _use_plain_commit(self):
        if self.plain_commit:
            text, _ = self.plain_commit
            self.plain_commit = None
            self.fresh = True
            self.mailbox.transcript('committed_transcript', text)

    def _confirm_until(self, seconds):
        """Forget replay audio up to `seconds` on this connection's audio clock."""
        if seconds is None:
            return
        first = self.session_sent - len(self.unconfirmed)   # Clock index of the oldest kept chunk.
        for _ in range(min(len(self.unconfirmed), int(seconds / CHUNK_SECONDS) - first)):
            self.unconfirmed.popleft()

    async def _send(self, ws, ready, attempt, reconnecting):
        await asyncio.wait_for(ready.wait(), timeout=8)
        if self.stopped.is_set():
            return
        attempt['live'] = True
        now = time.monotonic()
        if self.live_since is None:
            self.live_since = now
        self.fresh = False
        self.session_sent = 0
        stale_at = now + STALE_PARTIAL_AFTER if reconnecting and self.mailbox.speaking else None
        announce = reconnecting
        self.link = (LIVE, '')
        while not self.stopped.is_set():
            if self.pump.done() and not self.stopped.is_set():
                # A dead pump means no audio ever again: stop loudly instead of idling.
                raise UserError(tr('err.background'), fatal=True)
            now = time.monotonic()
            if self.lost:
                # The backlog overflowed: say so honestly, and do not pretend the sentence is whole.
                self.mailbox.interrupt()
                self.mailbox.gap(self.lost * CHUNK_SECONDS)
                self.lost, stale_at, announce = 0, None, False
            elif announce:
                self.note = (tr('status.reconnected'), now + 6)
                announce = False
            if stale_at and now > stale_at:
                if not self.fresh:
                    self.mailbox.interrupt()
                stale_at = None
            if not self.unsent:
                await asyncio.sleep(0.005)
                continue
            backlog = len(self.unsent)
            chunk = self.unsent.popleft()
            try:
                await asyncio.wait_for(ws.send(json.dumps(audio_message(chunk, RATE))), timeout=SEND_TIMEOUT)
            except BaseException:
                self.unsent.appendleft(chunk)
                raise
            self.unconfirmed.append(chunk)
            if len(self.unconfirmed) > UNCONFIRMED_CHUNKS:
                self.unconfirmed.popleft()
            self.sent_chunks += 1
            self.session_sent += 1
            if backlog > 2 and self.replay_speed:
                await asyncio.sleep(CHUNK_SECONDS / self.replay_speed)  # Catching up: pace the replay.

    # ── self-test ─────────────────────────────────────────────────────────────
    async def _selftest(self, key, seconds=3):
        feed = self._feed()
        peak = 0.0
        frames = 0
        try:
            self.mailbox.state(tr('test.audio', n=f'{seconds:g}'), BUSY)
            deadline = time.monotonic() + seconds
            while not self.stopped.is_set() and time.monotonic() < deadline:
                frames += len(feed.poll())
                peak = max(peak, feed.peak)
                self.mailbox.meter(feed.peak)
                await asyncio.sleep(0.02)
            problem = feed.problem
        finally:
            feed.close()
        self.mailbox.meter(0.0)
        if not frames:
            raise UserError(problem or tr('test.no_frames'))
        audio = tr('test.heard') if peak >= 0.001 else tr('test.silent')
        self.mailbox.state(tr('test.api'), BUSY)
        async with self.connector(connection_url(RATE, self.language), additional_headers={'xi-api-key': key},
                                  open_timeout=8, close_timeout=1, logger=_silent_logger()) as ws:
            started = False
            async with asyncio.timeout(8):
                async for raw in ws:
                    if decode_event(json.loads(raw), key)[0] == 'session_started':
                        started = True
                        break
            if not started:
                raise UserError(tr('test.no_session'))
        self.mailbox.state(tr('test.pass', audio=audio), OK)


def _quiet_socket_teardown(loop, context):
    """Windows' proactor loop reports WinError 10022 when shutting down an already-dead socket."""
    exc = context.get('exception')
    if isinstance(exc, OSError) and getattr(exc, 'winerror', None) == 10022:
        return
    loop.default_exception_handler(context)


def _silent_logger():
    logger = logging.Logger('scribe-private-socket')  # Never log headers (they carry the key).
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger

"""Engine and audio-feed behaviour against a real local WebSocket server (never the real API)."""
import asyncio
import json
import threading
from urllib.parse import urlparse

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from conftest import LOUD, SILENT
from linger import engine as engine_module
from linger.audio import AudioFeed, Source
from linger.engine import COMMIT, GAP, PROVISIONAL, Engine, Mailbox
from linger.errors import UserError

SOURCE = Source('', 'fake')


def output():
    return 'dev-1', 'Device one'


def local(port):
    def connector(url, **kwargs):
        assert url.startswith('wss://api.elevenlabs.io/')
        return connect(f'ws://127.0.0.1:{port}/?' + urlparse(url).query, proxy=None, **kwargs)
    return connector


def make(mailbox, fake_capture, **kwargs):
    kwargs.setdefault('retry_delays', (0.01,))
    kwargs.setdefault('replay_speed', None)
    return Engine(SOURCE, mailbox, capture_factory=fake_capture, default_output=output, **kwargs)


async def until(predicate, timeout=4):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


def collect(mailbox, events, statuses=None):
    snap = mailbox.drain()
    events.extend(snap.events)
    if statuses is not None and snap.status:
        statuses.append(snap.status)
    return events


def test_drop_mid_sentence_is_replayed_and_loses_nothing(fake_capture):
    async def scenario():
        requests, received = [], {1: 0, 2: 0}

        async def handler(ws):
            requests.append(ws.request)
            n = len(requests)
            await ws.send(json.dumps({'message_type': 'session_started'}))
            if n == 1:
                for _ in range(5):
                    await ws.recv()
                    received[1] += 1
                await ws.send(json.dumps({'message_type': 'partial_transcript', 'text': 'half a sen'}))
                await asyncio.sleep(0.05)
                return  # The server vanishes in the middle of a sentence.
            async for _ in ws:
                received[2] += 1
                if received[2] == 5:
                    await ws.send(json.dumps({'message_type': 'committed_transcript', 'text': 'Half a sentence, whole.'}))

        async with serve(handler, '127.0.0.1', 0) as server:
            m = Mailbox()
            e = make(m, fake_capture, connector=local(server.sockets[0].getsockname()[1]))
            task = asyncio.create_task(e._main())
            events, statuses = [], []
            await until(lambda: any(k == COMMIT for k, _ in collect(m, events, statuses)))
            # The new session re-heard the unfinished sentence: one clean commit, no gap, no leftovers.
            assert events == [(COMMIT, 'Half a sentence, whole.')]
            assert requests[0].headers['xi-api-key'] == 'offline-test-placeholder'
            assert 'language_code=en' in requests[0].path and 'audio_format=pcm_16000' in requests[0].path
            assert e.connections == 2 and received[2] >= 5
            await until(lambda: collect(m, events, statuses) is not None and any('没有丢失' in s for s in statuses))
            e.stop()
            await asyncio.wait_for(task, 3)
            assert all(c.closed for c in fake_capture.instances)
            assert len(fake_capture.instances) == 1  # Audio capture survived the reconnect untouched.
    asyncio.run(scenario())


def test_long_outage_reports_only_the_audio_actually_lost(fake_capture, monkeypatch):
    monkeypatch.setattr(engine_module, 'BACKLOG_CHUNKS', 10)

    async def scenario():
        sessions = []
        allow = asyncio.Event()

        async def handler(ws):
            sessions.append(ws)
            await ws.send(json.dumps({'message_type': 'session_started'}))
            if len(sessions) % 2:
                await ws.recv()
                await ws.send(json.dumps({'message_type': 'partial_transcript', 'text': 'cut off he'}))
                await asyncio.sleep(0.05)
                return
            async for _ in ws:
                pass

        async with serve(handler, '127.0.0.1', 0) as server:
            real = local(server.sockets[0].getsockname()[1])

            def connector(url, **kwargs):
                if len(sessions) % 2 and not allow.is_set():
                    raise OSError('network unreachable')  # The outage.
                return real(url, **kwargs)

            m = Mailbox()
            e = make(m, fake_capture, connector=connector)
            task = asyncio.create_task(e._main())
            events = []
            await until(lambda: len(sessions) == 1 and e.lost > 20)  # Far more audio than the backlog holds.
            allow.set()
            await until(lambda: any(k == GAP for k, _ in collect(m, events)))
            assert [k for k, _ in events] == [PROVISIONAL, GAP] and events[0][1] == 'cut off he'
            first_gap = events[1][1]
            assert first_gap > 2  # Seconds of audio dropped (the fake produces ten times real time).

            # The loss is counted in audio actually dropped and then cleared, so a later
            # interruption can never inherit the length of this one.
            assert first_gap == pytest.approx(round(first_gap, 1)) and e.lost == 0
            e.stop()
            await asyncio.wait_for(task, 3)
    asyncio.run(scenario())


def test_fatal_server_error_stops_without_retrying(fake_capture):
    async def scenario():
        attempts = []

        async def handler(ws):
            attempts.append(1)
            await ws.send(json.dumps({'message_type': 'auth_error', 'error': 'offline-test-placeholder leaked'}))
            await asyncio.sleep(1)

        async with serve(handler, '127.0.0.1', 0) as server:
            m = Mailbox()
            e = make(m, fake_capture, connector=local(server.sockets[0].getsockname()[1]))
            await asyncio.wait_for(e._main(), 3)
            assert len(attempts) == 1 and m.tone == 'error'
            assert 'sk_' in m.status and 'Key ID' in m.status  # The placeholder lacks the sk_ prefix.
            assert 'offline-test-placeholder' not in m.status and 'leaked' not in m.status
            assert all(c.closed for c in fake_capture.instances)
    asyncio.run(scenario())


def test_keeps_retrying_while_the_network_is_down(fake_capture):
    async def scenario():
        attempts = []

        def refuse(*args, **kwargs):
            attempts.append(1)
            raise OSError('headers xi-api-key: offline-test-placeholder')

        m = Mailbox()
        e = make(m, fake_capture, connector=refuse, retry_delays=(0.001,))
        task = asyncio.create_task(e._main())
        await until(lambda: len(attempts) > 30)
        assert not task.done() and m.tone == 'warn'
        assert 'offline-test-placeholder' not in m.status and 'headers' not in m.status
        e.stop()
        await asyncio.wait_for(task, 3)
        assert all(c.closed for c in fake_capture.instances)
    asyncio.run(scenario())


def test_stop_during_handshake_releases_everything(fake_capture):
    async def scenario():
        entered = asyncio.Event()

        class Pending:
            async def __aenter__(self):
                entered.set()
                await asyncio.sleep(20)

            async def __aexit__(self, *args):
                pass

        e = make(Mailbox(), fake_capture, connector=lambda *a, **k: Pending())
        task = asyncio.create_task(e._main())
        await entered.wait()
        e.stop()
        await asyncio.wait_for(task, 1)
        assert all(c.closed for c in fake_capture.instances)
        assert e.sent_chunks == 0 and e.mailbox.status == '已停止。'
    asyncio.run(scenario())


def test_blocked_send_reconnects_and_keeps_the_audio(fake_capture, monkeypatch):
    monkeypatch.setattr(engine_module, 'SEND_TIMEOUT', 0.1)

    async def scenario():
        class WS:
            def __init__(self):
                self.first = True

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self.first:
                    self.first = False
                    return json.dumps({'message_type': 'session_started'})
                await asyncio.sleep(30)

            async def send(self, message):
                await asyncio.sleep(30)

        class Connection:
            opened = 0

            async def __aenter__(self):
                Connection.opened += 1
                return WS()

            async def __aexit__(self, *args):
                pass

        m = Mailbox()
        e = make(m, fake_capture, connector=lambda *a, **k: Connection())
        task = asyncio.create_task(e._main())
        await until(lambda: Connection.opened >= 3)
        assert e.sent_chunks == 0 and len(e.unsent) > 0 and e.lost == 0  # Nothing sent, nothing thrown away.
        e.stop()
        await asyncio.wait_for(task, 3)
    asyncio.run(scenario())


def test_silent_from_the_start_points_at_the_routing(fake_capture, monkeypatch):
    monkeypatch.setattr(engine_module, 'NEVER_HEARD_AFTER', 0.1)
    fake_capture.payload = SILENT

    async def scenario():
        async def handler(ws):
            await ws.send(json.dumps({'message_type': 'session_started'}))
            async for _ in ws:
                pass

        async with serve(handler, '127.0.0.1', 0) as server:
            m = Mailbox()
            e = make(m, fake_capture, connector=local(server.sockets[0].getsockname()[1]))
            task = asyncio.create_task(e._main())
            await until(lambda: m.tone == 'quiet')
            assert '没有检测到声音' in m.status
            fake_capture.payload = LOUD  # Sound arrives: back to normal.
            await until(lambda: m.tone == 'live')
            e.stop()
            await asyncio.wait_for(task, 3)
    asyncio.run(scenario())


def test_selftest_checks_audio_and_api(fake_capture):
    async def scenario():
        async def handler(ws):
            await ws.send(json.dumps({'message_type': 'session_started'}))
            await asyncio.sleep(0.2)

        async with serve(handler, '127.0.0.1', 0) as server:
            m = Mailbox()
            e = make(m, fake_capture, mode='selftest', connector=local(server.sockets[0].getsockname()[1]))
            await asyncio.wait_for(e._selftest('k', seconds=0.1), 3)
            assert m.tone == 'ok' and '听到了声音' in m.status
            assert e.sent_chunks == 0 and fake_capture.instances[-1].closed
    asyncio.run(scenario())


def test_missing_key_fails_before_audio_or_network(monkeypatch, tmp_path):
    import winreg
    monkeypatch.delenv('ELEVENLABS_API_KEY')
    monkeypatch.setattr('linger.scribe.env_dirs', lambda: (tmp_path,))

    def missing(*args):
        raise FileNotFoundError()

    monkeypatch.setattr(winreg, 'OpenKey', missing)
    m = Mailbox()
    e = Engine(SOURCE, m, connector=lambda *a, **k: pytest.fail('connected'),
               capture_factory=lambda *a: pytest.fail('captured'), default_output=output)
    asyncio.run(e._main())
    assert m.tone == 'error' and 'API 密钥' in m.status


def test_mailbox_coalesces_partials_and_keeps_commit_order():
    m = Mailbox()
    for i in range(20000):
        m.transcript('partial_transcript', str(i))
    m.transcript('committed_transcript', 'First.')
    m.transcript('partial_transcript', 'Second')
    m.interrupt()
    m.transcript('partial_transcript', 'Third')
    snap = m.drain()
    assert snap.events == [(COMMIT, 'First.'), (PROVISIONAL, 'Second')] and snap.partial == 'Third'
    assert m.drain().events == [] and m.drain().partial is None


# ── audio feed ────────────────────────────────────────────────────────────────
def drain(feed, seconds=0.08):
    import time
    chunks, end = [], time.monotonic() + seconds
    while time.monotonic() < end:
        chunks += feed.poll()
        time.sleep(0.002)
    return chunks


def test_feed_follows_the_default_output_device(fake_capture, monkeypatch):
    monkeypatch.setattr(AudioFeed, 'FOLLOW_EVERY', 0.01)
    current = ['dev-1', 'Speakers']
    feed = AudioFeed(SOURCE, threading.Event(), fake_capture, lambda: tuple(current))
    assert drain(feed) and feed.bound == 'dev-1' and feed.switched is None
    current[:] = ['dev-2', 'Headphones']  # Headphones plugged in: Windows moves the default.
    assert drain(feed)
    assert feed.bound == 'dev-2' and feed.switched == 'Headphones'
    assert [c.source.uid for c in fake_capture.instances] == ['dev-1', 'dev-2']
    assert fake_capture.instances[0].closed and not fake_capture.instances[1].closed
    feed.close()

    # An explicitly chosen device is never swapped behind the user's back.
    fake_capture.instances.clear()
    fixed = AudioFeed(Source('dev-1', 'Speakers'), threading.Event(), fake_capture, lambda: ('dev-2', 'Headphones'))
    drain(fixed)
    assert fixed.bound == 'dev-1' and len(fake_capture.instances) == 1
    fixed.close()


def test_feed_reopens_after_a_capture_fault(fake_capture, monkeypatch):
    monkeypatch.setattr(AudioFeed, 'REOPEN_EVERY', 0.01)
    feed = AudioFeed(SOURCE, threading.Event(), fake_capture, output)
    assert drain(feed)
    broken = fake_capture.instances[0]

    def fail():
        raise UserError('capture broke')

    broken.take = fail
    feed.poll()
    assert feed.problem == 'capture broke' and broken.closed
    assert drain(feed) and feed.problem is None and len(fake_capture.instances) == 2  # No exception escaped.
    feed.close()


def test_feed_tracks_silence(fake_capture):
    fake_capture.payload = SILENT
    feed = AudioFeed(SOURCE, threading.Event(), fake_capture, output)
    drain(feed)
    assert not feed.heard and feed.silent_seconds > 0 and feed.peak == 0
    fake_capture.payload = LOUD
    drain(feed)
    assert feed.heard and feed.silent_seconds == 0
    feed.close()

"""Engine behaviour against a real local WebSocket server (never the real API)."""
import asyncio
import json
from urllib.parse import urlparse

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from linger.audio import Source
from linger.engine import COMMIT, GAP, PROVISIONAL, Engine, Mailbox

SOURCE = Source('', 'fake')


def local(port):
    def connector(url, **kwargs):
        assert url.startswith('wss://api.elevenlabs.io/')
        return connect(f'ws://127.0.0.1:{port}/?' + urlparse(url).query, proxy=None, **kwargs)
    return connector


async def until(predicate, timeout=3):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


def test_streams_transcripts_and_reconnects_after_a_drop(fake_capture):
    async def scenario():
        connections = []
        frames = []

        async def handler(ws):
            connections.append(ws.request)
            await ws.send(json.dumps({'message_type': 'session_started'}))
            if len(connections) == 1:
                await ws.recv()
                frames.append(1)
                await ws.send(json.dumps({'message_type': 'committed_transcript', 'text': 'Before the drop.'}))
                await ws.send(json.dumps({'message_type': 'partial_transcript', 'text': 'half a sen'}))
                await asyncio.sleep(0.05)
                return  # Server closes mid-sentence.
            await ws.send(json.dumps({'message_type': 'committed_transcript', 'text': 'After the drop.'}))
            async for _ in ws:
                frames.append(2)

        async with serve(handler, '127.0.0.1', 0) as server:
            port = server.sockets[0].getsockname()[1]
            m = Mailbox()
            engine = Engine(SOURCE, m, connector=local(port), capture_factory=fake_capture, retry_delays=(0.01,))
            task = asyncio.create_task(engine._main())
            events = []
            await until(lambda: events.extend(m.drain().events) or any(e[0] == COMMIT and 'After' in e[1] for e in events))
            assert [e for e in events if e[0] != GAP] == [(COMMIT, 'Before the drop.'), (PROVISIONAL, 'half a sen'),
                                                          (COMMIT, 'After the drop.')]
            assert any(e[0] == GAP for e in events)
            assert connections[0].headers['xi-api-key'] == 'offline-test-placeholder'
            assert 'language_code=en' in connections[0].path
            await until(lambda: 2 in frames)
            engine.stop()
            await asyncio.wait_for(task, 3)
            assert all(c.closed for c in fake_capture.instances)
            assert engine.connections == 2
    asyncio.run(scenario())


def test_fatal_server_error_stops_without_retrying(fake_capture):
    async def scenario():
        attempts = []

        async def handler(ws):
            attempts.append(1)
            await ws.send(json.dumps({'message_type': 'auth_error', 'error': 'offline-test-placeholder leaked'}))
            await asyncio.sleep(1)

        async with serve(handler, '127.0.0.1', 0) as server:
            port = server.sockets[0].getsockname()[1]
            m = Mailbox()
            engine = Engine(SOURCE, m, connector=local(port), capture_factory=fake_capture, retry_delays=(0.01,))
            await asyncio.wait_for(engine._main(), 3)
            assert len(attempts) == 1
            assert m.tone == 'error' and 'auth_error' in m.status
            assert 'offline-test-placeholder' not in m.status and 'leaked' not in m.status
            assert all(c.closed for c in fake_capture.instances)
    asyncio.run(scenario())


def test_gives_up_after_repeated_failed_connections(fake_capture):
    async def scenario():
        def refuse(*args, **kwargs):
            raise OSError('headers xi-api-key: offline-test-placeholder')

        m = Mailbox()
        engine = Engine(SOURCE, m, connector=refuse, capture_factory=fake_capture, retry_delays=(0.001,))
        await asyncio.wait_for(engine._main(), 3)
        assert m.tone == 'error' and '重连失败' in m.status
        assert 'offline-test-placeholder' not in m.status and 'headers' not in m.status
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

        engine = Engine(SOURCE, Mailbox(), connector=lambda *a, **k: Pending(), capture_factory=fake_capture)
        task = asyncio.create_task(engine._main())
        await entered.wait()
        engine.stop()
        await asyncio.wait_for(task, 1)
        assert fake_capture.instances[-1].closed and not fake_capture.instances[-1].started
        assert engine.sent_chunks == 0 and engine.mailbox.status == '已停止。'
    asyncio.run(scenario())


def test_blocked_send_is_retried_not_fatal(fake_capture):
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
        engine = Engine(SOURCE, m, connector=lambda *a, **k: Connection(), capture_factory=fake_capture,
                        retry_delays=(0.01,))
        task = asyncio.create_task(engine._main())
        await until(lambda: Connection.opened >= 2, timeout=5)  # Send timeout → reconnect.
        engine.stop()
        await asyncio.wait_for(task, 3)
        assert all(c.closed for c in fake_capture.instances)
    asyncio.run(scenario())


def test_selftest_checks_audio_and_api(fake_capture):
    async def scenario():
        async def handler(ws):
            await ws.send(json.dumps({'message_type': 'session_started'}))
            await asyncio.sleep(0.2)

        async with serve(handler, '127.0.0.1', 0) as server:
            port = server.sockets[0].getsockname()[1]
            m = Mailbox()
            engine = Engine(SOURCE, m, mode='selftest', connector=local(port), capture_factory=fake_capture)
            await asyncio.wait_for(engine._selftest('k', seconds=0.05), 3)
            assert m.tone == 'ok' and '听到了声音' in m.status
            assert engine.sent_chunks == 0 and fake_capture.instances[-1].closed
    asyncio.run(scenario())


def test_missing_key_fails_before_audio_or_network(monkeypatch, tmp_path):
    import winreg
    monkeypatch.delenv('ELEVENLABS_API_KEY')
    monkeypatch.setattr('linger.scribe.env_dirs', lambda: (tmp_path,))

    def missing(*args):
        raise FileNotFoundError()

    monkeypatch.setattr(winreg, 'OpenKey', missing)
    m = Mailbox()
    engine = Engine(SOURCE, m, connector=lambda *a, **k: pytest.fail('connected'),
                    capture_factory=lambda *a: pytest.fail('captured'))
    asyncio.run(engine._main())
    assert m.tone == 'error' and 'ELEVENLABS_API_KEY' in m.status


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

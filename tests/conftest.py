import threading
import time

import linger  # noqa: F401
import pytest

from linger import i18n

LOUD = b'\x00\x20' * 1600   # 100 ms of 16 kHz mono PCM16 at a quarter of full scale.
SILENT = bytes(3200)


@pytest.fixture(autouse=True)
def prohibit_external_network(monkeypatch):
    """Every test fails closed if code tries DNS or a non-local connection."""
    import socket
    original = socket.socket.connect

    def local_only(sock, address):
        if isinstance(address, tuple) and address[0] not in ('127.0.0.1', '::1'):
            raise AssertionError('External network prohibited in tests')
        return original(sock, address)

    def no_dns(*args, **kwargs):
        raise AssertionError('DNS prohibited in tests')

    monkeypatch.setattr(socket.socket, 'connect', local_only)
    monkeypatch.setattr(socket, 'getaddrinfo', no_dns)


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    """A placeholder key, no access to the real credential store, and a fixed interface language."""
    monkeypatch.setenv('ELEVENLABS_API_KEY', 'offline-test-placeholder')
    monkeypatch.setattr('linger.credentials.read', lambda *a, **k: '')
    i18n.set_language('zh')
    yield
    i18n.set_language('zh')


class FakeCapture:
    """Produces a 100 ms block every 10 ms (ten times real time). No devices are touched."""
    instances = []
    payload = LOUD

    def __init__(self, source, stopped):
        self.source = source
        self.stopped = stopped
        self.opened = self.started = self.closed = False
        self.next = 0
        self.instances.append(self)

    def open(self):
        self.opened = True

    def start(self):
        self.started = True

    def take(self):
        if self.stopped.is_set() or self.closed:
            return None
        if time.monotonic() >= self.next:
            self.next = time.monotonic() + 0.01
            return self.payload
        return None

    def close(self):
        self.closed = True


@pytest.fixture
def fake_capture():
    FakeCapture.instances.clear()
    FakeCapture.payload = LOUD
    yield FakeCapture
    FakeCapture.payload = LOUD


class Talker(threading.Thread):
    """Engine stand-in for UI tests: streams partials, commits every tenth. No audio, no network."""
    created = []

    def __init__(self, source, mailbox, *, mode='live', language='en'):
        super().__init__(daemon=False)
        self.mailbox, self.mode, self.language = mailbox, mode, language
        self.stopped = threading.Event()
        self.created.append(self)

    def stop(self):
        self.stopped.set()

    def run(self):
        if self.mode == 'selftest':
            self.mailbox.state('自检通过', 'ok')
            return
        self.mailbox.state('正在实时转写', 'live')
        i = 0
        while not self.stopped.wait(0.01):
            i += 1
            if i % 10:
                self.mailbox.transcript('partial_transcript', f'Speaking {i}')
            else:
                self.mailbox.transcript('committed_transcript', f'Sentence {i}.')
        self.mailbox.state('已停止。')


@pytest.fixture
def talker():
    Talker.created.clear()
    return Talker

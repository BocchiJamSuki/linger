import threading
import time

import linger  # noqa: F401
import pytest


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
def fake_key(monkeypatch):
    monkeypatch.setenv('ELEVENLABS_API_KEY', 'offline-test-placeholder')


class FakeCapture:
    """Produces a 100 ms block of quiet audio every 10 ms. No devices are touched."""
    instances = []

    def __init__(self, source, stopped):
        self.stopped = stopped
        self.opened = self.started = self.closed = False
        self.peak = 0.25
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
            return bytes(9600)
        return None

    def close(self):
        self.closed = True


@pytest.fixture
def fake_capture():
    FakeCapture.instances.clear()
    return FakeCapture


class Talker(threading.Thread):
    """Engine stand-in for UI tests: streams partials, commits every tenth. No audio, no network."""

    def __init__(self, source, mailbox, *, mode='live'):
        super().__init__(daemon=False)
        self.mailbox, self.mode = mailbox, mode
        self.stopped = threading.Event()

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
    return Talker

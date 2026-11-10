"""Audio conversion, protocol, credentials, transcript grouping and autosave."""
import base64
import importlib
import os
from urllib.parse import parse_qs, urlparse

import numpy as np
import pytest

from linger import credentials, scribe
from linger.audio import Leveler, Packetizer, mono_pcm, peak_of
from linger.errors import UserError
from linger.scribe import ServerEvent, api_key, audio_message, connection_url, decode_event
from linger.transcript import FINAL, GAP, PROVISIONAL, Recorder, Segment, join_speech, recent_group


def test_downmix_clipping_endian_and_mono_input():
    data = np.array([[1, 1], [-1, -1], [0.8, 0], [0, 0.8], [0.4, -0.4]], dtype='float32')
    pcm = mono_pcm(data)
    assert np.frombuffer(pcm, dtype='<i2').tolist() == [32767, -32768, 13107, 13107, 0]
    assert pcm[:4] == b'\xff\x7f\x00\x80'
    assert mono_pcm(np.array([np.nan, 2.0], dtype='float32')) == mono_pcm(np.array([[0.0], [1.0]]))
    assert peak_of(pcm) == pytest.approx(1.0) and peak_of(b'') == 0.0


def test_leveler_boosts_quiet_audio_without_touching_loud_or_silent():
    t = np.arange(1600) / 16000
    tone = np.sin(2 * np.pi * 440 * t).astype('float32')
    quiet = Leveler()
    out = [quiet.process(tone * 0.01) for _ in range(10)][-1]
    assert 0.2 < np.abs(out).max() <= 0.26                      # A 1% signal is raised to the target.
    assert np.abs(quiet.process(tone * 0.9)).max() <= 0.9 + 1e-6  # A sudden loud block is never amplified.
    loud = Leveler()
    assert np.allclose(loud.process(tone * 0.6), tone * 0.6)
    silent = Leveler()
    assert not silent.process(np.zeros(1600, dtype='float32')).any()
    faint = Leveler()
    assert np.abs(faint.process(tone * 1e-4)).max() <= 1e-4 * Leveler.MAX_GAIN + 1e-9  # Gain is capped.


@pytest.mark.parametrize('rate', [16000, 44100, 48000])
def test_packetization_never_loses_samples(rate):
    source = np.arange(rate, dtype='<i2').tobytes()
    p = Packetizer(rate)
    chunks = []
    for start in range(0, len(source), 274):
        chunks.extend(p.feed(source[start:start + 274]))
    assert len(chunks) == 10 and b''.join(chunks) == source and not p.pending


def test_protocol_parameters_and_payload():
    url = urlparse(connection_url(16000))
    q = parse_qs(url.query)
    assert url.scheme == 'wss' and url.netloc == 'api.elevenlabs.io'
    assert q['model_id'] == ['scribe_v2_realtime'] and q['language_code'] == ['en']
    assert q['commit_strategy'] == ['vad'] and q['audio_format'] == ['pcm_16000']
    assert parse_qs(urlparse(connection_url(16000, 'ja')).query)['language_code'] == ['ja']
    assert 'language_code' not in parse_qs(urlparse(connection_url(16000, None)).query)  # Auto-detect.
    msg = audio_message(b'\x00\x01' * 1600, 16000)
    assert msg['message_type'] == 'input_audio_chunk' and msg['commit'] is False
    assert len(base64.b64decode(msg['audio_base_64'])) == 3200


def test_server_errors_are_classified_and_never_echoed():
    with pytest.raises(ServerEvent) as fatal:
        decode_event({'message_type': 'auth_error', 'error': 'SECRET-VALUE'})
    assert fatal.value.fatal and 'SECRET' not in str(fatal.value)
    with pytest.raises(ServerEvent) as transient:
        decode_event({'message_type': 'session_time_limit_exceeded', 'error': 'SECRET'})
    assert not transient.value.fatal and 'SECRET' not in str(transient.value)
    with pytest.raises(ServerEvent) as unknown:
        decode_event({'message_type': 'weird_error', 'error': 'SECRET'})
    assert not unknown.value.fatal and 'server_error' in str(unknown.value)
    assert decode_event({'message_type': 'partial_transcript', 'text': 'hi'}) == ('partial_transcript', 'hi')
    with pytest.raises(UserError):
        decode_event({'message_type': 'committed_transcript', 'text': 3})


def test_key_id_pasted_as_key_gets_a_specific_hint():
    # ElevenLabs keys start with sk_; anything else is most likely the key's ID.
    with pytest.raises(ServerEvent) as wrong_kind:
        decode_event({'message_type': 'auth_error'}, key='0123abcd')
    assert 'Key ID' in str(wrong_kind.value) and '0123abcd' not in str(wrong_kind.value)
    with pytest.raises(ServerEvent) as plain:
        decode_event({'message_type': 'auth_error'}, key='sk_0123abcd')
    assert 'Key ID' not in str(plain.value)


@pytest.fixture
def no_saved_key(monkeypatch, tmp_path):
    import winreg
    monkeypatch.delenv('ELEVENLABS_API_KEY')

    def missing(*args):
        raise FileNotFoundError()

    monkeypatch.setattr(winreg, 'OpenKey', missing)
    monkeypatch.setattr('linger.scribe.env_dirs', lambda: (tmp_path,))
    return tmp_path


def test_key_from_env_files(no_saved_key):
    with pytest.raises(UserError) as error:
        api_key()
    assert error.value.fatal and scribe.find_key() == ('', None)
    # Notepad's BOM and quoted values are tolerated; .env wins over .env.txt.
    (no_saved_key / '.env.txt').write_text('ELEVENLABS_API_KEY="from-txt"\n', encoding='utf-8-sig')
    assert api_key() == 'from-txt'
    (no_saved_key / '.env').write_text('ELEVENLABS_API_KEY=from-env\n', encoding='utf-8')
    assert scribe.find_key() == ('from-env', 'file')


def test_key_saved_in_the_app_wins(monkeypatch):
    assert scribe.find_key() == ('offline-test-placeholder', 'env')
    monkeypatch.setattr(credentials, 'read', lambda *a, **k: 'sk_saved')
    assert scribe.find_key() == ('sk_saved', 'saved')


def test_credential_store_round_trip(monkeypatch):
    monkeypatch.undo()  # Drop the autouse stub: this test exercises the real Windows store.
    store = importlib.reload(credentials)
    target = f'Linger/Test-{os.getpid()}'
    try:
        assert store.read(target) == ''
        assert store.write('sk_test_密钥_123', target)
        assert store.read(target) == 'sk_test_密钥_123'
    finally:
        store.delete(target)
    assert store.read(target) == ''


def seg(text, start, end=None, kind=FINAL):
    return Segment(kind, text, start, end if end is not None else start + 1)


def test_recent_group_follows_short_pauses_only():
    segments = [seg('Earlier topic.', 0, 2), seg('Now, about your proposal,', 10, 12),
                seg('I mean the second one,', 13, 14), seg('how would you test it?', 15, 17)]
    assert recent_group(segments) == [1, 2, 3]
    assert recent_group(segments, limit=2) == [2, 3]
    assert recent_group(segments, extra=1) == [0, 1, 2, 3]
    # Words still being recognised are the newest piece (-1, reported last).
    assert recent_group(segments, partial=('and why', 18)) == [1, 2, 3, -1]
    assert recent_group(segments, partial=('Next question', 40)) == [-1]
    assert recent_group([]) == []


def test_recent_group_stops_at_connection_gap():
    segments = [seg('Before the drop', 0, 1), seg('gap', 1, 1, GAP), seg('After it', 2, 3, PROVISIONAL)]
    assert recent_group(segments) == [2]
    assert recent_group(segments, extra=1) == [0, 2]


def test_join_speech_reads_as_one_sentence():
    assert join_speech([' the second method, ', '', 'I mean the improved one.']) == \
        'the second method, I mean the improved one.'
    # Languages written without spaces join without one; mixed text keeps it.
    assert join_speech(['今天我们讨论', '实验结果。']) == '今天我们讨论实验结果。'
    assert join_speech(['これは', 'テストです']) == 'これはテストです'
    assert join_speech(['使用', 'Python 编写']) == '使用 Python 编写'


def test_recorder_writes_timestamped_lines(tmp_path):
    r = Recorder(tmp_path / 'records')
    assert r.write(seg('Hello there.', 1_800_000_000))
    assert r.write(seg('cut off', 1_800_000_005, kind=PROVISIONAL))
    assert r.write(seg('', 1_800_000_009, kind=GAP), note='连接中断约 2 秒')
    lines = r.path.read_text(encoding='utf-8').splitlines()
    assert lines[0].endswith('] Hello there.') and lines[1].endswith('cut off（未确认）')
    assert '连接中断约 2 秒' in lines[2]
    first = r.path
    r.new_file()
    r.write(seg('Next interview.', 1_800_000_000))
    assert r.path != first and r.path.exists()  # Same minute: never overwrites.

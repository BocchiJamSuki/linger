"""Audio conversion, protocol, credentials, transcript grouping and autosave."""
import base64
from urllib.parse import parse_qs, urlparse

import numpy as np
import pytest

from linger.audio import Packetizer, mono_pcm, peak_of
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


@pytest.mark.parametrize('rate', [16000, 44100, 48000])
def test_packetization_never_loses_samples(rate):
    source = np.arange(rate, dtype='<i2').tobytes()
    p = Packetizer(rate)
    chunks = []
    for start in range(0, len(source), 274):
        chunks.extend(p.feed(source[start:start + 274]))
    assert len(chunks) == 10 and b''.join(chunks) == source and not p.pending


def test_protocol_parameters_and_payload():
    url = urlparse(connection_url(48000))
    q = parse_qs(url.query)
    assert url.scheme == 'wss' and url.netloc == 'api.elevenlabs.io'
    assert q['model_id'] == ['scribe_v2_realtime'] and q['language_code'] == ['en']
    assert q['commit_strategy'] == ['vad'] and q['audio_format'] == ['pcm_48000']
    msg = audio_message(b'\x00\x01' * 4800, 48000)
    assert msg['message_type'] == 'input_audio_chunk' and msg['commit'] is False
    assert len(base64.b64decode(msg['audio_base_64'])) == 9600


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
    assert error.value.fatal
    # Notepad's BOM and quoted values are tolerated; .env wins over .env.txt.
    (no_saved_key / '.env.txt').write_text('ELEVENLABS_API_KEY="from-txt"\n', encoding='utf-8-sig')
    assert api_key() == 'from-txt'
    (no_saved_key / '.env').write_text('ELEVENLABS_API_KEY=from-env\n', encoding='utf-8')
    assert api_key() == 'from-env'


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

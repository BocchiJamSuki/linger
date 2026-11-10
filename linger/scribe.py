"""ElevenLabs Scribe realtime protocol: credentials, connection URL, messages, events."""
import base64
import os
from pathlib import Path
import sys
from urllib.parse import urlencode

from . import credentials
from .errors import UserError
from .i18n import tr

ENDPOINT = 'wss://api.elevenlabs.io/v1/speech-to-text/realtime'
KEY_NAME = 'ELEVENLABS_API_KEY'
KEY_PREFIX = 'sk_'


def env_dirs():
    dirs = [Path.cwd(), Path(__file__).resolve().parent.parent]
    if getattr(sys, 'frozen', False):  # Packaged build: next to the executable.
        dirs.insert(0, Path(sys.executable).resolve().parent)
    return dirs


def _from_env_files():
    # '.env.txt' covers files saved from Notepad while Explorer hides extensions.
    for base in env_dirs():
        for name in ('.env', '.env.txt'):
            path = base / name
            if not path.is_file():
                continue
            try:
                for line in path.read_text(encoding='utf-8-sig').splitlines():
                    line = line.strip()
                    if line.startswith(KEY_NAME + '='):
                        value = line.split('=', 1)[1].strip().strip('"').strip("'")
                        if value:
                            return value
            except OSError:
                pass
    return ''


def _from_registry():
    """Saved Windows variables that this process may not have inherited yet."""
    if os.name != 'nt':
        return ''
    import winreg
    for root, path in ((winreg.HKEY_CURRENT_USER, r'Environment'),
                       (winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment')):
        try:
            with winreg.OpenKey(root, path) as key:
                value = str(winreg.QueryValueEx(key, KEY_NAME)[0]).strip()
                if value:
                    return value
        except OSError:
            pass
    return ''


def find_key():
    """→ (key, source) with source 'saved' | 'env' | 'file' | None. A key set in the app wins."""
    for source, lookup in (('saved', credentials.read),
                           ('env', lambda: os.getenv(KEY_NAME, '').strip() or _from_registry()),
                           ('file', _from_env_files)):
        value = lookup().strip()
        if value:
            return value, source
    return '', None


def api_key():
    value, _ = find_key()
    if not value:
        raise UserError(tr('err.no_key'), fatal=True)
    return value


def connection_url(rate, language='en'):
    params = dict(
        model_id='scribe_v2_realtime', audio_format=f'pcm_{rate}',
        commit_strategy='vad', vad_silence_threshold_secs=0.7, vad_threshold=0.4,
        min_speech_duration_ms=100, min_silence_duration_ms=100,
        # Word timings tell exactly how much audio each commit covers (see Engine._receive).
        include_timestamps='true', include_language_detection='false',
    )
    if language:  # Omitted: the service detects the language.
        params['language_code'] = language
    return ENDPOINT + '?' + urlencode(params)


def audio_message(pcm, rate):
    return dict(message_type='input_audio_chunk', audio_base_64=base64.b64encode(pcm).decode('ascii'),
                sample_rate=rate, commit=False)


# Retrying cannot fix these; everything else is treated as transient and reconnected.
FATAL = {
    'auth_error': 'err.auth',
    'quota_exceeded': 'err.quota',
    'unaccepted_terms': 'err.terms',
    'invalid_request': 'err.request',
    'input_error': 'err.request',
    'chunk_size_exceeded': 'err.request',
}
TRANSIENT = {
    'error', 'throttled', 'rate_limited', 'queue_overflow', 'resource_exhausted',
    'session_time_limit_exceeded', 'insufficient_audio_activity', 'transcriber_error',
}
SESSION_LIMIT = 'session_time_limit_exceeded'
TIMED_COMMIT = 'committed_transcript_with_timestamps'


class ServerEvent(UserError):
    def __init__(self, kind, message, fatal):
        super().__init__(message, fatal=fatal)
        self.kind = kind


def committed_until(event):
    """Seconds of this session's audio that a timed commit covers: the end of its last word."""
    ends = [w.get('end') for w in event.get('words') or [] if isinstance(w, dict)]
    return max((e for e in ends if isinstance(e, (int, float))), default=None)


def decode_event(event, key=''):
    """→ (kind, text). Raises ServerEvent for errors; never echoes server-provided text."""
    kind = event.get('message_type', '')
    if kind in FATAL:
        message = FATAL[kind]
        if kind == 'auth_error' and key and not key.startswith(KEY_PREFIX):
            message = 'err.auth_id'  # The commonest mistake: pasting the key's ID instead of the key.
        raise ServerEvent(kind, tr(message, kind=kind), True)
    if kind in TRANSIENT or kind.endswith('_error') or 'error' in event:
        known = kind if kind in TRANSIENT else 'server_error'
        raise ServerEvent(known, tr('err.transient', kind=known), False)
    if kind in ('partial_transcript', 'committed_transcript', TIMED_COMMIT):
        text = event.get('text', '')
        if not isinstance(text, str):
            raise UserError(tr('err.bad_message'))
        return kind, text
    return kind, ''

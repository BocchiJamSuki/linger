"""ElevenLabs Scribe realtime protocol: credentials, connection URL, messages, events."""
import base64
import os
from pathlib import Path
from urllib.parse import urlencode

from .errors import UserError

ENDPOINT = 'wss://api.elevenlabs.io/v1/speech-to-text/realtime'
KEY_NAME = 'ELEVENLABS_API_KEY'


def env_dirs():
    return Path.cwd(), Path(__file__).resolve().parent.parent


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


def api_key():
    value = os.getenv(KEY_NAME, '').strip() or _from_registry() or _from_env_files()
    if not value:
        raise UserError(f'没有找到 {KEY_NAME}。请设置环境变量，或在程序目录的 .env 文件中写入 {KEY_NAME}=…',
                        fatal=True)
    return value


def connection_url(rate):
    return ENDPOINT + '?' + urlencode(dict(
        model_id='scribe_v2_realtime', audio_format=f'pcm_{rate}', language_code='en',
        commit_strategy='vad', vad_silence_threshold_secs=0.7, vad_threshold=0.4,
        min_speech_duration_ms=100, min_silence_duration_ms=100,
        include_timestamps='false', include_language_detection='false',
    ))


def audio_message(pcm, rate):
    return dict(message_type='input_audio_chunk', audio_base_64=base64.b64encode(pcm).decode('ascii'),
                sample_rate=rate, commit=False)


# Retrying cannot fix these; everything else is treated as transient and reconnected.
FATAL = {
    'auth_error': '密钥无效或无权限（auth_error）。请检查 ELEVENLABS_API_KEY。',
    'quota_exceeded': 'ElevenLabs 额度已用完（quota_exceeded）。',
    'unaccepted_terms': '需要先在 ElevenLabs 网站接受服务条款（unaccepted_terms）。',
    'invalid_request': '请求参数被拒绝（invalid_request）。',
    'input_error': '音频格式被拒绝（input_error）。',
    'chunk_size_exceeded': '音频分块过大（chunk_size_exceeded）。',
}
TRANSIENT = {
    'error', 'throttled', 'rate_limited', 'queue_overflow', 'resource_exhausted',
    'session_time_limit_exceeded', 'insufficient_audio_activity', 'transcriber_error',
}
SESSION_LIMIT = 'session_time_limit_exceeded'


class ServerEvent(UserError):
    def __init__(self, kind, message, fatal):
        super().__init__(message, fatal=fatal)
        self.kind = kind


def decode_event(event):
    """→ (kind, text). Raises ServerEvent for errors; never echoes server-provided text."""
    kind = event.get('message_type', '')
    if kind in FATAL:
        raise ServerEvent(kind, FATAL[kind], True)
    if kind in TRANSIENT or kind.endswith('_error') or 'error' in event:
        known = kind if kind in TRANSIENT else 'server_error'
        raise ServerEvent(known, f'ElevenLabs 返回 {known}。', False)
    if kind in ('partial_transcript', 'committed_transcript'):
        text = event.get('text', '')
        if not isinstance(text, str):
            raise UserError('收到无效的字幕消息。')
        return kind, text
    return kind, ''

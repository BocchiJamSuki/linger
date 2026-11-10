"""The API key in Windows Credential Manager: encrypted per user, never in a file."""
import sys

TARGET = 'Linger/ElevenLabsApiKey'
_GENERIC, _LOCAL_MACHINE = 1, 2


def _api():
    import ctypes
    from ctypes import wintypes

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [('Flags', wintypes.DWORD), ('Type', wintypes.DWORD), ('TargetName', wintypes.LPWSTR),
                    ('Comment', wintypes.LPWSTR), ('LastWritten', wintypes.FILETIME),
                    ('CredentialBlobSize', wintypes.DWORD), ('CredentialBlob', ctypes.POINTER(ctypes.c_ubyte)),
                    ('Persist', wintypes.DWORD), ('AttributeCount', wintypes.DWORD),
                    ('Attributes', ctypes.c_void_p), ('TargetAlias', wintypes.LPWSTR), ('UserName', wintypes.LPWSTR)]

    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    advapi.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
    advapi.CredReadW.restype = wintypes.BOOL
    advapi.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), wintypes.DWORD]
    advapi.CredWriteW.restype = wintypes.BOOL
    advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    advapi.CredDeleteW.restype = wintypes.BOOL
    advapi.CredFree.argtypes = [ctypes.c_void_p]
    return ctypes, advapi, CREDENTIAL


def read(target=TARGET):
    """The saved secret, or '' when there is none."""
    if sys.platform != 'win32':
        return ''
    ctypes, advapi, CREDENTIAL = _api()
    pointer = ctypes.POINTER(CREDENTIAL)()
    if not advapi.CredReadW(target, _GENERIC, 0, ctypes.byref(pointer)):
        return ''
    try:
        cred = pointer.contents
        return ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize).decode('utf-16-le')
    finally:
        advapi.CredFree(pointer)


def write(secret, target=TARGET):
    if sys.platform != 'win32':
        return False
    ctypes, advapi, CREDENTIAL = _api()
    blob = secret.encode('utf-16-le')
    cred = CREDENTIAL()
    cred.Type, cred.Persist = _GENERIC, _LOCAL_MACHINE
    cred.TargetName, cred.UserName = target, 'Linger'
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(ctypes.create_string_buffer(blob, len(blob)), ctypes.POINTER(ctypes.c_ubyte))
    return bool(advapi.CredWriteW(ctypes.byref(cred), 0))


def delete(target=TARGET):
    if sys.platform != 'win32':
        return False
    _, advapi, _ = _api()
    return bool(advapi.CredDeleteW(target, _GENERIC, 0))

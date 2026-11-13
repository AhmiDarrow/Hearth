"""Read an Authenticode signer thumbprint with Windows APIs. No PowerShell."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

TRUST_E_NOSIGNATURE = 0x800B0100
TRUST_E_BAD_DIGEST = 0x80096010
TRUST_E_SUBJECT_NOT_TRUSTED = 0x800B0004
CERT_E_UNTRUSTEDROOT = 0x800B0109
CERT_E_CHAINING = 0x800B010A
CERT_E_EXPIRED = 0x800B0101
CERT_E_CRITICAL = 0x800B010C
CERT_E_WRONG_USAGE = 0x800B0110

CERT_QUERY_OBJECT_FILE = 1
CERT_QUERY_CONTENT_FLAG_PKCS7_SIGNED_EMBED = 0x00000400
CERT_QUERY_FORMAT_FLAG_BINARY = 0x00000002
CMSG_SIGNER_INFO_PARAM = 6
CERT_HASH_PROP_ID = 3
CERT_FIND_SUBJECT_CERT = 0x000B0000
X509_ASN_ENCODING = 0x00000001
PKCS_7_ASN_ENCODING = 0x00010000
ENCODING = X509_ASN_ENCODING | PKCS_7_ASN_ENCODING

WTD_UI_NONE = 2
WTD_REVOKE_NONE = 0
WTD_CHOICE_FILE = 1
WTD_STATEACTION_VERIFY = 1
WTD_STATEACTION_CLOSE = 2
WTD_REVOCATION_CHECK_NONE = 0x10
WTD_CACHE_ONLY_URL_RETRIEVAL = 0x1000

HCERTSTORE = wintypes.HANDLE
HCRYPTMSG = wintypes.HANDLE
PCCERT_CONTEXT = ctypes.c_void_p


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


# WINTRUST_ACTION_GENERIC_VERIFY_V2
WINTRUST_ACTION_GENERIC_VERIFY_V2 = GUID(
    0x00AAC56B,
    0xCD44,
    0x11D0,
    (0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE),
)


class CRYPT_INTEGER_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


class CRYPT_ALGORITHM_IDENTIFIER(ctypes.Structure):
    _fields_ = [("pszObjId", ctypes.c_char_p), ("Parameters", CRYPT_INTEGER_BLOB)]


class CRYPT_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("cAttr", wintypes.DWORD), ("rgAttr", ctypes.c_void_p)]


class CMSG_SIGNER_INFO(ctypes.Structure):
    _fields_ = [
        ("dwVersion", wintypes.DWORD),
        ("Issuer", CRYPT_INTEGER_BLOB),
        ("SerialNumber", CRYPT_INTEGER_BLOB),
        ("HashAlgorithm", CRYPT_ALGORITHM_IDENTIFIER),
        ("HashEncryptionAlgorithm", CRYPT_ALGORITHM_IDENTIFIER),
        ("EncryptedHash", CRYPT_INTEGER_BLOB),
        ("AuthAttrs", CRYPT_ATTRIBUTES),
        ("UnauthAttrs", CRYPT_ATTRIBUTES),
    ]


class CERT_INFO_FIND(ctypes.Structure):
    """Leading fields of CERT_INFO, enough for CERT_FIND_SUBJECT_CERT."""

    _fields_ = [
        ("dwVersion", wintypes.DWORD),
        ("SerialNumber", CRYPT_INTEGER_BLOB),
        ("SignatureAlgorithm", CRYPT_ALGORITHM_IDENTIFIER),
        ("Issuer", CRYPT_INTEGER_BLOB),
    ]


class WINTRUST_FILE_INFO(ctypes.Structure):
    _fields_ = [
        ("cbStruct", wintypes.DWORD),
        ("pcwszFilePath", wintypes.LPCWSTR),
        ("hFile", wintypes.HANDLE),
        ("pgKnownSubject", ctypes.c_void_p),
    ]


class WINTRUST_DATA(ctypes.Structure):
    _fields_ = [
        ("cbStruct", wintypes.DWORD),
        ("pPolicyCallbackData", ctypes.c_void_p),
        ("pSIPClientData", ctypes.c_void_p),
        ("dwUIChoice", wintypes.DWORD),
        ("fdwRevocationChecks", wintypes.DWORD),
        ("dwUnionChoice", wintypes.DWORD),
        ("pFile", ctypes.POINTER(WINTRUST_FILE_INFO)),
        ("dwStateAction", wintypes.DWORD),
        ("hWVTStateData", wintypes.HANDLE),
        ("pwszURLReference", wintypes.LPCWSTR),
        ("dwProvFlags", wintypes.DWORD),
        ("dwUIContext", wintypes.DWORD),
        ("pSignatureSettings", ctypes.c_void_p),
    ]


def _bind_win32() -> tuple[ctypes.WinDLL, ctypes.WinDLL]:
    """Bind crypt32/wintrust with 64-bit-safe argtypes.

    Without these prototypes, ctypes treats arguments and pointer-sized
    return values as 32-bit c_int. On 64-bit Windows that truncates
    pointers (ASLR often places them above 4 GiB), so CryptQueryObject
    and CertFindCertificateInStore fail and the signer thumbprint comes
    back empty. v0.3.4's updater then reports a correctly signed
    installer as "not Authenticode-signed".
    """
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    crypt32.CryptQueryObject.argtypes = [
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(HCERTSTORE),
        ctypes.POINTER(HCRYPTMSG),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    crypt32.CryptQueryObject.restype = wintypes.BOOL
    crypt32.CryptMsgGetParam.argtypes = [
        HCRYPTMSG,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
    ]
    crypt32.CryptMsgGetParam.restype = wintypes.BOOL
    crypt32.CryptMsgClose.argtypes = [HCRYPTMSG]
    crypt32.CryptMsgClose.restype = wintypes.BOOL
    crypt32.CertFindCertificateInStore.argtypes = [
        HCERTSTORE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        PCCERT_CONTEXT,
    ]
    # Must be a pointer, not the default c_int: a 64-bit PCCERT_CONTEXT
    # would otherwise be truncated the same way as the arguments above.
    crypt32.CertFindCertificateInStore.restype = PCCERT_CONTEXT
    crypt32.CertGetCertificateContextProperty.argtypes = [
        PCCERT_CONTEXT,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
    ]
    crypt32.CertGetCertificateContextProperty.restype = wintypes.BOOL
    crypt32.CertFreeCertificateContext.argtypes = [PCCERT_CONTEXT]
    crypt32.CertFreeCertificateContext.restype = wintypes.BOOL
    crypt32.CertCloseStore.argtypes = [HCERTSTORE, wintypes.DWORD]
    crypt32.CertCloseStore.restype = wintypes.BOOL

    wintrust = ctypes.WinDLL("wintrust", use_last_error=True)
    wintrust.WinVerifyTrust.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(GUID),
        ctypes.c_void_p,
    ]
    wintrust.WinVerifyTrust.restype = ctypes.c_long
    return crypt32, wintrust


def _as_uint32(value: int) -> int:
    return value & 0xFFFFFFFF


def _trust_status(hresult: int) -> str:
    code = _as_uint32(hresult)
    if code == 0:
        return "Valid"
    if code == TRUST_E_NOSIGNATURE:
        return "NotSigned"
    if code == TRUST_E_BAD_DIGEST:
        return "HashMismatch"
    if code in {
        TRUST_E_SUBJECT_NOT_TRUSTED,
        CERT_E_UNTRUSTEDROOT,
        CERT_E_CHAINING,
        CERT_E_EXPIRED,
        CERT_E_CRITICAL,
        CERT_E_WRONG_USAGE,
    }:
        # Self-signed installers often land here until the publisher cert is trusted.
        return "NotTrusted"
    return f"UnknownError:0x{code:08X}"


def _signer_thumbprint(path: Path, crypt32: ctypes.WinDLL) -> str:
    encoding = wintypes.DWORD()
    content_type = wintypes.DWORD()
    format_type = wintypes.DWORD()
    store = HCERTSTORE()
    msg = HCRYPTMSG()
    # Keep the wide path alive for the duration of the call.
    path_wchar = wintypes.LPCWSTR(str(path))
    if not crypt32.CryptQueryObject(
        CERT_QUERY_OBJECT_FILE,
        path_wchar,
        CERT_QUERY_CONTENT_FLAG_PKCS7_SIGNED_EMBED,
        CERT_QUERY_FORMAT_FLAG_BINARY,
        0,
        ctypes.byref(encoding),
        ctypes.byref(content_type),
        ctypes.byref(format_type),
        ctypes.byref(store),
        ctypes.byref(msg),
        None,
    ):
        raise OSError("CryptQueryObject failed")
    try:
        needed = wintypes.DWORD()
        if not crypt32.CryptMsgGetParam(msg, CMSG_SIGNER_INFO_PARAM, 0, None, ctypes.byref(needed)):
            raise OSError("CryptMsgGetParam size failed")
        buf = ctypes.create_string_buffer(needed.value)
        if not crypt32.CryptMsgGetParam(msg, CMSG_SIGNER_INFO_PARAM, 0, buf, ctypes.byref(needed)):
            raise OSError("CryptMsgGetParam failed")
        info = CMSG_SIGNER_INFO.from_buffer(buf)
        find = CERT_INFO_FIND()
        find.SerialNumber = info.SerialNumber
        find.Issuer = info.Issuer
        ctx = crypt32.CertFindCertificateInStore(
            store,
            ENCODING,
            0,
            CERT_FIND_SUBJECT_CERT,
            ctypes.byref(find),
            None,
        )
        if not ctx:
            raise OSError("CertFindCertificateInStore failed")
        try:
            size = wintypes.DWORD()
            if not crypt32.CertGetCertificateContextProperty(ctx, CERT_HASH_PROP_ID, None, ctypes.byref(size)):
                raise OSError("CertGetCertificateContextProperty size failed")
            digest = ctypes.create_string_buffer(size.value)
            if not crypt32.CertGetCertificateContextProperty(ctx, CERT_HASH_PROP_ID, digest, ctypes.byref(size)):
                raise OSError("CertGetCertificateContextProperty failed")
            return digest.raw[: size.value].hex().upper()
        finally:
            crypt32.CertFreeCertificateContext(ctx)
    finally:
        if msg:
            crypt32.CryptMsgClose(msg)
        if store:
            crypt32.CertCloseStore(store, 0)


def _win_verify_trust(path: Path, wintrust: ctypes.WinDLL) -> int:
    file_info = WINTRUST_FILE_INFO()
    file_info.cbStruct = ctypes.sizeof(WINTRUST_FILE_INFO)
    # Keep the path string alive while WinVerifyTrust runs.
    path_text = str(path)
    file_info.pcwszFilePath = path_text
    file_info.hFile = None
    file_info.pgKnownSubject = None

    data = WINTRUST_DATA()
    ctypes.memset(ctypes.byref(data), 0, ctypes.sizeof(data))
    data.cbStruct = ctypes.sizeof(WINTRUST_DATA)
    data.dwUIChoice = WTD_UI_NONE
    data.fdwRevocationChecks = WTD_REVOKE_NONE
    data.dwUnionChoice = WTD_CHOICE_FILE
    data.pFile = ctypes.pointer(file_info)
    data.dwStateAction = WTD_STATEACTION_VERIFY
    data.dwProvFlags = WTD_REVOCATION_CHECK_NONE | WTD_CACHE_ONLY_URL_RETRIEVAL

    policy = WINTRUST_ACTION_GENERIC_VERIFY_V2
    result = wintrust.WinVerifyTrust(None, ctypes.byref(policy), ctypes.byref(data))
    data.dwStateAction = WTD_STATEACTION_CLOSE
    wintrust.WinVerifyTrust(None, ctypes.byref(policy), ctypes.byref(data))
    return int(result)


def authenticode_info(path: Path) -> dict[str, str] | None:
    """Return {status, thumbprint} for a PE file. Windows only; None on failure."""
    if sys.platform != "win32":
        return None
    target = Path(path)
    if not target.is_file():
        return None
    try:
        crypt32, wintrust = _bind_win32()
    except OSError:
        return None
    try:
        status = _trust_status(_win_verify_trust(target, wintrust))
    except OSError:
        status = "UnknownError"
    thumbprint = ""
    try:
        thumbprint = _signer_thumbprint(target, crypt32)
    except OSError:
        pass
    return {"status": status, "thumbprint": thumbprint}

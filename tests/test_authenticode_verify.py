"""Authenticode acceptance policy: pin to our thumbprint, allow self-signed chain status."""

from __future__ import annotations

from pathlib import Path

import pytest

from hearth import update


CERT = update.CERT_THUMBPRINT


def _info(monkeypatch, status: str, thumbprint: str) -> None:
    monkeypatch.setattr(
        update,
        "authenticode_info",
        lambda path: {"status": status, "thumbprint": thumbprint},
    )


def _verify(monkeypatch, status: str, thumbprint: str) -> None:
    _info(monkeypatch, status, thumbprint)
    update.verify_authenticode(Path("HearthSetup.exe"), CERT)


def test_accepts_valid_and_self_signed_statuses_when_thumbprint_matches(monkeypatch) -> None:
    for status in (
        "Valid",
        "NotTrusted",
        "UnknownError",
        "UnknownError:0x800B0109",
        "UnknownError:0x8009601E",
    ):
        _verify(monkeypatch, status, CERT)


def test_accepts_colon_separated_thumbprint(monkeypatch) -> None:
    pretty = "C7:B8:07:D5:42:B3:BA:72:07:AA:BE:D9:60:F5:56:B4:DC:85:C0:2F"
    _verify(monkeypatch, "NotTrusted", pretty)


def test_accepts_not_signed_if_thumbprint_still_matches(monkeypatch) -> None:
    # A self-signed file can be mis-reported as NotSigned by some trust
    # policies; if we can still read our publisher thumbprint, accept it.
    _verify(monkeypatch, "NotSigned", CERT)


def test_rejects_hash_mismatch_even_with_matching_thumbprint(monkeypatch) -> None:
    with pytest.raises(ValueError, match="HashMismatch"):
        _verify(monkeypatch, "HashMismatch", CERT)


def test_empty_thumbprint_is_the_v034_failure_mode(monkeypatch) -> None:
    # v0.3.4 failed to extract the thumbprint on 64-bit Windows because
    # CryptQueryObject / CertFindCertificateInStore lacked ctypes argtypes,
    # so a correctly signed, NotTrusted self-signed installer was reported
    # as "not Authenticode-signed".
    with pytest.raises(ValueError, match="not Authenticode-signed"):
        _verify(monkeypatch, "NotTrusted", "")


def test_rejects_wrong_publisher(monkeypatch) -> None:
    with pytest.raises(ValueError, match="publisher certificate"):
        _verify(monkeypatch, "NotTrusted", "A" * 40)


def test_rejects_missing_signature_when_no_thumbprint(monkeypatch) -> None:
    with pytest.raises(ValueError, match="not valid \\(NotSigned\\)"):
        _verify(monkeypatch, "NotSigned", "")


def test_v034_policy_accepts_not_trusted_when_thumbprint_is_present() -> None:
    """v0.3.4 already allowed NotTrusted; it failed only when the thumbprint was empty."""

    def v034(info: dict[str, str]) -> None:
        status = info["status"]
        if status in {"NotSigned", "HashMismatch"}:
            raise ValueError(f"Installer signature is not valid ({status})")
        got = info["thumbprint"]
        if not got:
            raise ValueError("Installer is not Authenticode-signed")
        if got != CERT:
            raise ValueError("Installer is not signed by the Hearth publisher certificate")

    v034({"status": "NotTrusted", "thumbprint": CERT})
    with pytest.raises(ValueError, match="not Authenticode-signed"):
        v034({"status": "NotTrusted", "thumbprint": ""})
    with pytest.raises(ValueError, match="not valid \\(NotSigned\\)"):
        v034({"status": "NotSigned", "thumbprint": CERT})

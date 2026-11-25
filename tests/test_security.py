"""GitHub signature verification."""

from __future__ import annotations

from switchyard.security import sign, verify_signature

BODY = b'{"zen": "keep it logically awesome"}'
SECRET = "s3cret"


def test_a_correct_signature_verifies() -> None:
    assert verify_signature(SECRET, BODY, sign(SECRET, BODY))


def test_a_wrong_secret_does_not() -> None:
    assert not verify_signature("other", BODY, sign(SECRET, BODY))


def test_a_modified_body_does_not() -> None:
    assert not verify_signature(SECRET, BODY + b" ", sign(SECRET, BODY))


def test_missing_and_malformed_headers_do_not() -> None:
    assert not verify_signature(SECRET, BODY, None)
    assert not verify_signature(SECRET, BODY, "")
    assert not verify_signature(SECRET, BODY, "sha1=abc")
    assert not verify_signature("", BODY, sign(SECRET, BODY))


def test_signature_is_prefixed_for_github() -> None:
    assert sign(SECRET, BODY).startswith("sha256=")

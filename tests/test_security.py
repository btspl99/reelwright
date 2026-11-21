import time

from reelwright import security as s


def test_password_roundtrip():
    h = s.hash_password("correct-horse-1")
    assert s.verify_password("correct-horse-1", h)
    assert not s.verify_password("wrong", h)
    assert h != s.hash_password("correct-horse-1")  # salted


def test_token_roundtrip_and_tamper():
    t = s.create_token("sec", "u1", 1)
    assert s.decode_token("sec", t) == "u1"
    assert s.decode_token("other", t) is None
    assert s.decode_token("sec", t[:-2] + "xx") is None


def test_api_key_hash():
    plain, prefix, digest = s.generate_api_key()
    assert plain.startswith("rw_") and plain.startswith(prefix)
    assert s.hash_api_key(plain) == digest


def test_webhook_signature():
    body = b'{"a":1}'
    header = s.sign_webhook("k", body)
    assert s.verify_webhook("k", body, header)
    assert not s.verify_webhook("k", b'{"a":2}', header)
    assert not s.verify_webhook("k", body, s.sign_webhook("k", body, timestamp=int(time.time()) - 4000))


def test_media_signature():
    exp = int(time.time()) + 60
    sig = s.sign_media("k", "c1", "video", exp)
    assert s.verify_media("k", "c1", "video", exp, sig)
    assert not s.verify_media("k", "c2", "video", exp, sig)
    assert not s.verify_media("k", "c1", "video", int(time.time()) - 1, s.sign_media("k", "c1", "video", int(time.time()) - 1))

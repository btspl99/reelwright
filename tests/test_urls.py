import pytest

from reelwright.pipeline.urls import UnsafeURLError, validate_source_url


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/a.mp4", "http://localhost/a", "http://169.254.169.254/latest/meta-data",
    "http://10.0.0.5/x", "file:///etc/passwd", "ftp://example.com/x", "http://[::1]/x", "not a url",
])
def test_blocks_unsafe(url):
    with pytest.raises(UnsafeURLError):
        validate_source_url(url)


def test_allows_private_when_enabled():
    assert validate_source_url("http://127.0.0.1/a.mp4", allow_private=True)


def test_allows_public():
    assert validate_source_url("https://example.com/v.mp4").startswith("https://example.com")

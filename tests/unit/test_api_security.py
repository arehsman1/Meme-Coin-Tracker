"""API security helpers — rate limiter and path classification."""

from meme_tracker.api.rate_limit import (
    SECURITY_HEADERS,
    SlidingWindowLimiter,
    is_write_path,
)


def test_sliding_window_allows_then_blocks():
    lim = SlidingWindowLimiter(limit=3, window_seconds=60.0)
    now = 1000.0
    assert lim.allow("ip1", now=now) is True
    assert lim.allow("ip1", now=now + 0.1) is True
    assert lim.allow("ip1", now=now + 0.2) is True
    assert lim.allow("ip1", now=now + 0.3) is False
    assert lim.allow("ip2", now=now + 0.3) is True
    assert lim.allow("ip1", now=now + 61.0) is True


def test_remaining_counts_down():
    lim = SlidingWindowLimiter(limit=2, window_seconds=10.0)
    now = 50.0
    lim.allow("a", now=now)
    assert lim.remaining("a", now=now) == 1
    lim.allow("a", now=now + 0.1)
    assert lim.remaining("a", now=now + 0.1) == 0


def test_write_paths_detected():
    assert is_write_path("/api/v1/tokens/evaluate") is True
    assert is_write_path("/api/v1/wallets/track") is True
    assert is_write_path("/api/v1/admin/scanner/settings") is True
    assert is_write_path("/api/v1/tokens") is False
    assert is_write_path("/health") is False


def test_security_headers_present():
    assert "X-Content-Type-Options" in SECURITY_HEADERS
    assert SECURITY_HEADERS["X-Frame-Options"] == "DENY"

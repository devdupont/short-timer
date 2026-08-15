"""Rate limiting: the budget itself, and the client-address it's counted against.

Getting the address resolution wrong is a security bug rather than a cosmetic
one: if a caller can influence the address we attribute a request to, they
can defeat the login limit by varying it on every attempt.
"""

import pytest
from fastapi import Request

from shortimer.config import get_settings
from shortimer.util.ratelimit import RateLimit, client_ip, enforce


def _request(headers: dict[str, str], peer: str = "10.0.0.9") -> Request:
    """A bare ASGI `Request` carrying `headers` and a socket peer of `peer`."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (peer, 1234),
    }
    return Request(scope)


# --- The budget ----------------------------------------------------------------


async def test_enforce_allows_up_to_the_limit_then_blocks() -> None:
    """`enforce` allows exactly `limit` calls, then the next raises 429 with a Retry-After."""
    limit = RateLimit("test-scope", limit=3, window_seconds=3600)
    for _ in range(3):
        await enforce(limit, "subject-a")

    with pytest.raises(Exception) as excinfo:
        await enforce(limit, "subject-a")
    assert getattr(excinfo.value, "status_code", None) == 429
    # Clients are told when to come back.
    assert "Retry-After" in getattr(excinfo.value, "headers", {})


async def test_limits_are_tracked_per_subject() -> None:
    """Two different subjects each get their own independent budget."""
    limit = RateLimit("test-scope", limit=1, window_seconds=3600)
    await enforce(limit, "subject-a")
    # A different subject has its own budget.
    await enforce(limit, "subject-b")


# --- The address a budget is counted against ------------------------------------


def test_headers_are_ignored_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Secure by default: an unconfigured deployment trusts only the socket."""
    monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
    monkeypatch.delenv("CLIENT_IP_HEADER", raising=False)
    request = _request({"x-forwarded-for": "1.2.3.4"}, peer="10.0.0.9")
    assert client_ip(request) == "10.0.0.9"


def test_spoofed_forwarded_for_cannot_displace_the_real_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The attack this guards against, behind one appending proxy.

    The caller sends their own X-Forwarded-For; the proxy appends the address
    it actually saw. Taking the leftmost entry would hand the attacker a fresh
    rate-limit bucket per request.
    """
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    get_settings.cache_clear()
    request = _request({"x-forwarded-for": "9.9.9.9, 203.0.113.7"})
    assert client_ip(request) == "203.0.113.7"


def test_varying_the_spoofed_value_does_not_change_the_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Five different attacker-supplied leftmost values all resolve to the one real address."""
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    get_settings.cache_clear()
    seen = {
        client_ip(_request({"x-forwarded-for": f"{i}.{i}.{i}.{i}, 203.0.113.7"}))
        for i in range(1, 6)
    }
    assert seen == {"203.0.113.7"}


def test_multiple_trusted_hops_count_in_from_the_right(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With two trusted proxies, the real client is the second entry from the right."""
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "2")
    get_settings.cache_clear()
    request = _request({"x-forwarded-for": "9.9.9.9, 203.0.113.7, 10.1.1.1"})
    assert client_ip(request) == "203.0.113.7"


def test_short_header_falls_back_rather_than_trusting_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fewer entries than trusted hops means it didn't take the expected path."""
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "2")
    get_settings.cache_clear()
    request = _request({"x-forwarded-for": "9.9.9.9"}, peer="10.0.0.9")
    assert client_ip(request) == "10.0.0.9"


def test_platform_header_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Single-value headers like Fly-Client-IP have no list to mis-parse."""
    monkeypatch.setenv("CLIENT_IP_HEADER", "fly-client-ip")
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    get_settings.cache_clear()
    request = _request({"fly-client-ip": "198.51.100.4", "x-forwarded-for": "9.9.9.9, 1.1.1.1"})
    assert client_ip(request) == "198.51.100.4"


def test_missing_platform_header_falls_through_to_forwarded_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A platform header is configured but absent on this request; X-Forwarded-For still works."""
    monkeypatch.setenv("CLIENT_IP_HEADER", "fly-client-ip")
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    get_settings.cache_clear()
    request = _request({"x-forwarded-for": "9.9.9.9, 203.0.113.7"})
    assert client_ip(request) == "203.0.113.7"


def test_no_client_and_no_headers_is_still_a_usable_key() -> None:
    """No socket peer and no headers at all still returns a string, not a crash."""
    scope: dict[str, object] = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "client": None,
    }
    assert client_ip(Request(scope)) == "unknown"

"""Sprint 2 — AW-4: exponential backoff with jitter, capped, Retry-After honoured."""

from __future__ import annotations

from src.llm.azure_openai import _retry_after_seconds, _retry_delay


class _FakeResponse:
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


class _FakeRateLimit(Exception):
    def __init__(self, headers: dict[str, str]) -> None:
        super().__init__("429")
        self.response = _FakeResponse(headers)


def test_retry_after_parsed_from_header() -> None:
    assert _retry_after_seconds(_FakeRateLimit({"retry-after": "2"})) == 2.0
    assert _retry_after_seconds(_FakeRateLimit({"Retry-After": "5"})) == 5.0


def test_retry_after_absent_or_invalid_returns_none() -> None:
    assert _retry_after_seconds(_FakeRateLimit({})) is None
    assert _retry_after_seconds(_FakeRateLimit({"retry-after": "soon"})) is None
    assert _retry_after_seconds(Exception("no response attr")) is None


def test_backoff_respects_retry_after() -> None:
    # Server said "wait 2s" → delay is ~2s (+<=1s jitter), never the exponential.
    for _ in range(50):
        d = _retry_delay(attempt=1, base=15.0, cap=120.0, retry_after=2.0)
        assert 2.0 <= d <= 3.0


def test_backoff_is_exponential_with_full_jitter() -> None:
    # attempt 1 ∈ [0,15], attempt 3 ∈ [0,60] — growing, jittered.
    for _ in range(100):
        d1 = _retry_delay(attempt=1, base=15.0, cap=120.0)
        d3 = _retry_delay(attempt=3, base=15.0, cap=120.0)
        assert 0.0 <= d1 <= 15.0
        assert 0.0 <= d3 <= 60.0


def test_backoff_is_capped() -> None:
    # Without a cap, base 15 ** attempt would explode; here it is bounded.
    for _ in range(100):
        d = _retry_delay(attempt=10, base=15.0, cap=120.0)
        assert 0.0 <= d <= 120.0

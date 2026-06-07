from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.llm.azure_openai import AzureOpenAILLM


class _StubCompletions:
    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    async def create(self, **_kwargs):
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _make_response(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
    )


def _make_llm(
    outcomes: list[object], *, max_retries: int
) -> tuple[AzureOpenAILLM, _StubCompletions]:
    llm = AzureOpenAILLM.__new__(AzureOpenAILLM)
    completions = _StubCompletions(outcomes)
    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    llm.model_main = "gpt-4o"
    llm.model_mini = "gpt-4o-mini"
    llm.max_retries = max_retries
    llm.retry_backoff_seconds = 0.0
    llm.max_backoff_seconds = 120.0
    llm._use_max_completion_tokens = False
    llm._supports_temperature = True
    return llm, completions


def test_default_deployment_is_gpt_4_1_mini(monkeypatch: pytest.MonkeyPatch) -> None:
    """C3: default deployment falls back to gpt-4.1-mini when no env override."""
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://x.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "dummy-key")
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT_MAIN", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT_MINI", raising=False)

    llm = AzureOpenAILLM()

    assert llm.model_main == "gpt-4.1-mini"
    assert llm.model_mini == "gpt-4.1-mini"


def test_deployment_env_override_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """C3: explicit env override takes precedence over the default."""
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://x.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "dummy-key")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT_MAIN", "custom-main")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT_MINI", "custom-mini")

    llm = AzureOpenAILLM()

    assert llm.model_main == "custom-main"
    assert llm.model_mini == "custom-mini"


class _ParamSensitiveCompletions:
    """Stub that rejects max_tokens / non-default temperature like gpt-5.x."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if "max_tokens" in kwargs:
            raise RuntimeError(
                "Error code: 400 - Unsupported parameter: 'max_tokens' is not "
                "supported with this model. Use 'max_completion_tokens' instead."
            )
        if "temperature" in kwargs and kwargs["temperature"] != 1:
            raise RuntimeError(
                "Error code: 400 - Unsupported value: 'temperature' does not "
                "support 0.0 with this model."
            )
        return _make_response('{"ok": true}')


@pytest.mark.asyncio
async def test_adapts_to_max_completion_tokens_and_temperature() -> None:
    """C3 follow-up: newer models requiring max_completion_tokens are handled."""
    llm = AzureOpenAILLM.__new__(AzureOpenAILLM)
    stub = _ParamSensitiveCompletions()
    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=stub))
    llm.model_main = "gpt-5.4"
    llm.model_mini = "gpt-5.4"
    llm.max_retries = 3
    llm.retry_backoff_seconds = 0.0
    llm.max_backoff_seconds = 0.0
    llm._use_max_completion_tokens = False
    llm._supports_temperature = True

    response = await llm.complete(
        system="s", user="u", json_mode=True, temperature=0.0, max_tokens=50
    )

    assert response.content == '{"ok": true}'
    assert llm._use_max_completion_tokens is True
    assert llm._supports_temperature is False
    final = stub.calls[-1]
    assert "max_completion_tokens" in final
    assert "max_tokens" not in final
    assert "temperature" not in final


@pytest.mark.asyncio
async def test_complete_retries_transient_failure_then_succeeds() -> None:
    llm, completions = _make_llm(
        [RuntimeError("transient timeout"), _make_response('{"ok": true}')],
        max_retries=2,
    )

    response = await llm.complete(
        system="system",
        user="user",
        json_mode=True,
    )

    assert completions.calls == 2
    assert response.content == '{"ok": true}'
    assert response.tokens_input == 11
    assert response.tokens_output == 7


@pytest.mark.asyncio
async def test_complete_raises_after_retry_budget_is_exhausted() -> None:
    llm, completions = _make_llm(
        [RuntimeError("timeout-1"), RuntimeError("timeout-2")],
        max_retries=2,
    )

    with pytest.raises(RuntimeError, match="timeout-2"):
        await llm.complete(system="system", user="user")

    assert completions.calls == 2

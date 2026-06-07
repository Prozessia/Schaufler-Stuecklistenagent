"""Azure OpenAI LLM implementation — DSGVO-konform, EU-Region."""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time

from src.llm.base import BaseLLM, LLMResponse

logger = logging.getLogger(__name__)


def _retry_after_seconds(exc: Exception) -> float | None:
    """Extract a server-provided ``Retry-After`` (seconds) from an API error.

    Returns None when the header is absent or unparsable. Honouring this on 429
    is the difference between cooperating with the rate limiter and hammering it.
    """
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None) if resp is not None else None
    if not headers:
        return None
    try:
        raw = headers.get("retry-after") or headers.get("Retry-After")
    except AttributeError:
        return None
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def _retry_delay(
    attempt: int,
    *,
    base: float,
    cap: float,
    retry_after: float | None = None,
) -> float:
    """Compute the wait before the next retry.

    AW-4: true exponential backoff with full jitter, capped — and a
    server-provided ``Retry-After`` always wins (plus a small jitter so parallel
    callers do not wake in lock-step). ``attempt`` is 1-based.
    """
    if retry_after is not None:
        return min(retry_after, cap) + random.uniform(0.0, 1.0)
    exponential = base * (2 ** max(0, attempt - 1))
    return random.uniform(0.0, min(exponential, cap))


class AzureOpenAILLM(BaseLLM):
    """Azure OpenAI implementation (default model: gpt-4.1-mini, Sweden Central).

    Requires the ``openai`` package and the following env vars:
        AZURE_OPENAI_ENDPOINT
        AZURE_OPENAI_KEY
        AZURE_OPENAI_API_VERSION  (default: 2024-10-21)
        AZURE_OPENAI_DEPLOYMENT_MAIN  (default: gpt-4.1-mini)
        AZURE_OPENAI_DEPLOYMENT_MINI  (default: gpt-4.1-mini)
    """

    def __init__(self) -> None:
        try:
            from openai import AsyncAzureOpenAI
        except ImportError as exc:
            raise ImportError(
                "pip install openai  — required for Azure OpenAI"
            ) from exc

        import httpx

        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
        key = os.environ.get("AZURE_OPENAI_KEY", "")
        api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")

        if not endpoint or not key:
            raise EnvironmentError(
                "AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY must be set"
            )

        self.client = AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=key,
            api_version=api_version,
            timeout=httpx.Timeout(300.0, connect=30.0),
        )
        self.model_main = os.environ.get("AZURE_OPENAI_DEPLOYMENT_MAIN", "gpt-4.1-mini")
        self.model_mini = os.environ.get("AZURE_OPENAI_DEPLOYMENT_MINI", "gpt-4.1-mini")
        self.max_retries = max(1, int(os.environ.get("MAX_LLM_RETRIES", "3")))
        self.retry_backoff_seconds = float(
            os.environ.get("LLM_RETRY_BACKOFF_SECONDS", "15.0")
        )
        # AW-4: cap the exponential backoff so a high base cannot explode into
        # multi-minute sleeps (the old `base ** attempt` reached ~56 min).
        self.max_backoff_seconds = float(
            os.environ.get("LLM_RETRY_MAX_BACKOFF_SECONDS", "120.0")
        )
        # Model-capability flags, learned on first 400 and remembered per instance.
        # Newer Azure models (gpt-5.x / o-series) require ``max_completion_tokens``
        # instead of ``max_tokens`` and only accept the default ``temperature``.
        self._use_max_completion_tokens = False
        self._supports_temperature = True

    def _build_request_params(
        self,
        *,
        model: str,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        extra: dict,
    ) -> dict:
        """Assemble create() params, honoring learned model-capability flags."""
        params: dict = {"model": model, "messages": messages, **extra}
        if self._use_max_completion_tokens:
            params["max_completion_tokens"] = max_tokens
        else:
            params["max_tokens"] = max_tokens
        if self._supports_temperature:
            params["temperature"] = temperature
        return params

    def _adapt_unsupported_parameter(self, exc: Exception) -> bool:
        """Learn from a 400 about unsupported params. Returns True if adapted.

        Handles the newer-model requirements:
          * ``max_tokens`` → ``max_completion_tokens``
          * ``temperature`` other than default → drop the parameter
        """
        message = str(getattr(exc, "message", "") or exc)
        lowered = message.lower()
        if "unsupported" not in lowered and "not supported" not in lowered:
            return False

        adapted = False
        if (
            "max_completion_tokens" in message or "max_tokens" in message
        ) and not self._use_max_completion_tokens:
            self._use_max_completion_tokens = True
            logger.info(
                "Model requires 'max_completion_tokens'; switching parameter name."
            )
            adapted = True
        if "temperature" in lowered and self._supports_temperature:
            self._supports_temperature = False
            logger.info(
                "Model does not support custom 'temperature'; using model default."
            )
            adapted = True
        return adapted

    async def _create_completion_with_retry(
        self,
        *,
        operation: str,
        model: str,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        **kwargs,
    ):
        last_exc: Exception | None = None
        attempt = 0
        param_adaptations = 0

        while attempt < self.max_retries:
            attempt += 1
            t0 = time.perf_counter()
            try:
                params = self._build_request_params(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    extra=kwargs,
                )
                response = await self.client.chat.completions.create(**params)
                latency = (time.perf_counter() - t0) * 1000
                return response, latency
            except Exception as exc:  # noqa: BLE001
                # Deterministic parameter incompatibility (400): adapt and retry
                # immediately without consuming a transient-retry attempt.
                if self._adapt_unsupported_parameter(exc):
                    param_adaptations += 1
                    if param_adaptations <= 3:
                        attempt -= 1
                        continue

                last_exc = exc
                if attempt >= self.max_retries:
                    raise
                wait_time = _retry_delay(
                    attempt,
                    base=self.retry_backoff_seconds,
                    cap=self.max_backoff_seconds,
                    retry_after=_retry_after_seconds(exc),
                )
                logger.warning(
                    "%s failed on attempt %d/%d: %s | retrying in %.2fs",
                    operation,
                    attempt,
                    self.max_retries,
                    exc,
                    wait_time,
                )
                await asyncio.sleep(wait_time)

        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"{operation} failed without an exception")

    async def complete(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        use_mini: bool = False,
    ) -> LLMResponse:
        model = self.model_mini if use_mini else self.model_main
        kwargs: dict = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response, latency = await self._create_completion_with_retry(
            operation="Azure OpenAI text completion",
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

        choice = response.choices[0]
        usage = response.usage

        return LLMResponse(
            content=choice.message.content or "",
            tokens_input=usage.prompt_tokens if usage else 0,
            tokens_output=usage.completion_tokens if usage else 0,
            model=model,
            latency_ms=latency,
        )

    async def complete_with_image(
        self,
        system: str,
        user: str,
        image_b64: str,
        *,
        json_mode: bool = False,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        # Design decision: image completions always use model_main (gpt-4.1-mini).
        # The Vision counter-check needs full-capability image understanding for
        # per-field forensic verification; gpt-4.1-mini supports vision and is
        # used for both text and image calls. A use_mini parameter is
        # deliberately not offered here to prevent accidental quality
        # degradation via a weaker deployment.
        kwargs: dict = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response, latency = await self._create_completion_with_retry(
            operation="Azure OpenAI image completion",
            model=self.model_main,
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{image_b64}",
                                "detail": "high",
                            },
                        },
                    ],
                },
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        choice = response.choices[0]
        usage = response.usage

        return LLMResponse(
            content=choice.message.content or "",
            tokens_input=usage.prompt_tokens if usage else 0,
            tokens_output=usage.completion_tokens if usage else 0,
            model=self.model_main,
            latency_ms=latency,
        )

    async def complete_with_images(
        self,
        system: str,
        user: str,
        images_b64: list[str],
        *,
        json_mode: bool = False,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        kwargs: dict = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        content: list[dict] = [{"type": "text", "text": user}]
        for img_b64 in images_b64:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{img_b64}",
                        "detail": "high",
                    },
                }
            )

        response, latency = await self._create_completion_with_retry(
            operation="Azure OpenAI multi-image completion",
            model=self.model_main,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        choice = response.choices[0]
        usage = response.usage

        return LLMResponse(
            content=choice.message.content or "",
            tokens_input=usage.prompt_tokens if usage else 0,
            tokens_output=usage.completion_tokens if usage else 0,
            model=self.model_main,
            latency_ms=latency,
        )

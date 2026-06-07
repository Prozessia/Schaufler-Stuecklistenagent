"""Abstract LLM interface — provider-agnostic base class."""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field


class LLMResponse(BaseModel):
    """Standardized response from any LLM provider."""

    content: str
    tokens_input: int = 0
    tokens_output: int = 0
    model: str = ""
    latency_ms: float = 0.0


class BaseLLM(ABC):
    """Abstract base for LLM providers.

    All LLM interactions go through this interface so the provider
    can be swapped without changing business logic.
    """

    @abstractmethod
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
        """Send a text completion request."""
        ...

    @abstractmethod
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
        """Send a completion request that includes a base64 image."""
        ...

    @abstractmethod
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
        """Send a completion request that includes multiple base64 images."""
        ...

"""Which providers exist, and which one serves a given model name.

Phase 2 uses the simplest possible addressing: `"<provider>/<model>"`, e.g.
`groq/llama-3.3-70b-versatile` or `mock/any-name`. Phase 4 replaces this with
route config (public model names, fallbacks) stored in Postgres.
"""

import httpx

from app.config import Settings
from app.errors import ModelNotFound
from app.providers.base import Provider
from app.providers.groq import GroqProvider
from app.providers.mock import MockProvider
from app.providers.openrouter import OpenRouterProvider


class ProviderRegistry:
    def __init__(self, providers: list[Provider]) -> None:
        self._providers = {p.name: p for p in providers}

    @property
    def names(self) -> list[str]:
        return sorted(self._providers)

    def resolve(self, model: str) -> tuple[Provider, str]:
        """Split `"groq/llama-3.3-70b"` into (GroqProvider, `"llama-3.3-70b"`).

        Only the first slash separates: OpenRouter's own model IDs contain
        slashes (`openrouter/meta-llama/llama-3.3-70b-instruct`).
        """
        provider_name, sep, upstream_model = model.partition("/")
        if not sep or not upstream_model:
            raise ModelNotFound(
                f"model '{model}' must be given as '<provider>/<model>'; "
                f"available providers: {', '.join(self.names)}"
            )
        provider = self._providers.get(provider_name)
        if provider is None:
            raise ModelNotFound(
                f"provider '{provider_name}' is not configured; "
                f"available providers: {', '.join(self.names)}"
            )
        return provider, upstream_model


def build_registry(settings: Settings, client: httpx.AsyncClient) -> ProviderRegistry:
    """Register the mock always, and each real provider that has an API key."""
    providers: list[Provider] = [
        MockProvider(
            latency_s=settings.mock_latency_ms / 1000,
            failure_rate=settings.mock_failure_rate,
            completion_tokens=settings.mock_completion_tokens,
        )
    ]
    if settings.groq_api_key is not None:
        providers.append(
            GroqProvider(
                api_key=settings.groq_api_key.get_secret_value(),
                base_url=settings.groq_base_url,
                client=client,
            )
        )
    if settings.openrouter_api_key is not None:
        providers.append(
            OpenRouterProvider(
                api_key=settings.openrouter_api_key.get_secret_value(),
                base_url=settings.openrouter_base_url,
                client=client,
            )
        )
    return ProviderRegistry(providers)

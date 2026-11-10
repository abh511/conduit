import httpx

from app.providers.openai_compat import OpenAICompatibleProvider


class OpenRouterProvider(OpenAICompatibleProvider):
    def __init__(self, *, api_key: str, base_url: str, client: httpx.AsyncClient) -> None:
        super().__init__(
            name="openrouter",
            base_url=base_url,
            api_key=api_key,
            client=client,
            # Optional header OpenRouter uses to attribute traffic to an app.
            extra_headers={"X-Title": "Conduit"},
        )

import httpx

from app.providers.openai_compat import OpenAICompatibleProvider


class GroqProvider(OpenAICompatibleProvider):
    def __init__(self, *, api_key: str, base_url: str, client: httpx.AsyncClient) -> None:
        super().__init__(name="groq", base_url=base_url, api_key=api_key, client=client)

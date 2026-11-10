"""OpenAI-compatible request/response models for chat completions.

Only the fields Conduit itself needs are declared. Everything else
(`tools`, `response_format`, `seed`, ...) is accepted via `extra="allow"` and
forwarded untouched, so the gateway doesn't have to be updated every time a
provider adds a parameter.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _OpenAIModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class ChatMessage(_OpenAIModel):
    role: str
    # A string, or a list of content parts (text/image), or null for
    # assistant messages that only carry tool calls.
    content: str | list[dict[str, Any]] | None = None


class ChatRequest(_OpenAIModel):
    model: str = Field(min_length=1)
    messages: list[ChatMessage] = Field(min_length=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, gt=0)
    stream: bool = False


class Usage(_OpenAIModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class Choice(_OpenAIModel):
    index: int = 0
    message: ChatMessage
    finish_reason: str | None = None


class ChatResponse(_OpenAIModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[Choice]
    usage: Usage | None = None

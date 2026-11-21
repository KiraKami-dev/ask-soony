"""
Provider-neutral agent loop interface.

The agent only knows about text, images and tools. Each backend translates that into its own
API: llm/anthropic_backend.py (Anthropic SDK) and llm/openai_backend.py (OpenAI SDK, also for
OpenAI-compatible servers such as llama.cpp).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from ask_soony.extract import Image


@dataclass
class Tool:
    name: str
    description: str
    schema: dict


@dataclass
class RunResult:
    final_text: str
    tool_calls: int
    stop_reason: str
    usage: dict = field(default_factory=dict)


# Runs one tool call and returns the text result. Raising ToolError marks the result as an error.
ToolHandler = Callable[[str, dict], str]


class ToolError(Exception):
    pass


class Backend(Protocol):
    provider: str
    model: str

    def run(
        self,
        system: str,
        text: str,
        images: list[Image],
        tools: list[Tool],
        handler: ToolHandler,
        should_stop: Callable[[], bool],
        max_turns: int = 12,
    ) -> RunResult: ...


def make_backend(provider: str, model: str, api_key: str, base_url: str, effort: str) -> Backend:
    if provider == "anthropic":
        from ask_soony.llm.anthropic_backend import AnthropicBackend

        return AnthropicBackend(model=model, api_key=api_key, base_url=base_url, effort=effort)
    if provider in {"openai", "llamacpp", "openai-compatible"}:
        from ask_soony.llm.openai_backend import OpenAIBackend

        return OpenAIBackend(model=model, api_key=api_key, base_url=base_url, provider=provider)
    raise ValueError(f"unknown ASK_LLM_PROVIDER {provider!r}: use anthropic, openai or llamacpp")

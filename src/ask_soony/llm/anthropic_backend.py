"""Claude backend: manual tool loop on the Messages API via the official Anthropic SDK."""

import base64
from collections.abc import Callable

import anthropic

from ask_soony.extract import Image
from ask_soony.llm import RunResult, Tool, ToolError, ToolHandler

# Server-side refusal fallback: on a policy decline the API re-runs the request on a fallback
# model inside the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = {
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-sonnet-5-5",
    "claude-fable-5-1",
    "claude-fable-5-2",
}


class AnthropicBackend:
    provider = "anthropic"

    def __init__(self, model: str, api_key: str = "", base_url: str = "", effort: str = "medium"):
        kwargs = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        # Without an explicit key the SDK resolves ANTHROPIC_API_KEY or an `ant auth login` profile.
        self.client = anthropic.Anthropic(max_retries=4, **kwargs)
        self.model = model
        self.effort = effort

    def run(
        self,
        system: str,
        text: str,
        images: list[Image],
        tools: list[Tool],
        handler: ToolHandler,
        should_stop: Callable[[], bool],
        max_turns: int = 12,
    ) -> RunResult:
        content: list[dict] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img.media_type,
                    "data": base64.standard_b64encode(img.data).decode("ascii"),
                },
            }
            for img in images
        ]
        content.append({"type": "text", "text": text})
        messages: list[dict] = [{"role": "user", "content": content}]
        tool_defs = [
            {"name": t.name, "description": t.description, "input_schema": t.schema} for t in tools
        ]
        calls = 0
        usage = {"input_tokens": 0, "output_tokens": 0}
        stop = "max_turns"
        final_text = ""
        for _ in range(max_turns):
            params = dict(
                model=self.model,
                max_tokens=16000,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                tools=tool_defs,
                messages=messages,
                output_config={"effort": self.effort},
            )
            if self.model in FALLBACK_MODELS:
                response = self.client.beta.messages.create(
                    **params, betas=[FALLBACK_BETA], fallbacks="default"
                )
            else:
                response = self.client.messages.create(**params)
            usage["input_tokens"] += response.usage.input_tokens
            usage["output_tokens"] += response.usage.output_tokens
            stop = response.stop_reason or "unknown"
            final_text = "\n".join(b.text for b in response.content if b.type == "text")
            if stop == "refusal":
                break
            # Keep the whole assistant turn (thinking and tool_use blocks) unchanged.
            messages.append({"role": "assistant", "content": response.content})
            if stop == "pause_turn":
                continue
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if stop != "tool_use" or not tool_uses:
                break
            results = []
            for block in tool_uses:
                calls += 1
                try:
                    out = handler(block.name, dict(block.input))
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": out})
                except ToolError as e:
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(e),
                            "is_error": True,
                        }
                    )
            messages.append({"role": "user", "content": results})
            if should_stop():
                stop = "submitted"
                break
        return RunResult(final_text=final_text, tool_calls=calls, stop_reason=stop, usage=usage)

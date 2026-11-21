"""
OpenAI-compatible backend: OpenAI itself, or a local llama.cpp server (`llama-server --jinja`,
which speaks the same chat completions API with tool calls) via ASK_LLM_BASE_URL.
"""

import base64
import json
from collections.abc import Callable

from openai import OpenAI

from ask_soony.extract import Image
from ask_soony.llm import RunResult, Tool, ToolError, ToolHandler


class OpenAIBackend:
    def __init__(self, model: str, api_key: str = "", base_url: str = "", provider: str = "openai"):
        kwargs = {}
        if base_url:
            kwargs["base_url"] = base_url
        # Local servers ignore the key, but the SDK requires one.
        if api_key or base_url:
            kwargs["api_key"] = api_key or "local"
        self.client = OpenAI(max_retries=4, **kwargs)
        self.model = model
        self.provider = provider

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
        content: list[dict] = [{"type": "text", "text": text}]
        for img in images:
            b64 = base64.standard_b64encode(img.data).decode("ascii")
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:{img.media_type};base64,{b64}"}}
            )
        messages: list[dict] = [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ]
        tool_defs = [
            {
                "type": "function",
                "function": {"name": t.name, "description": t.description, "parameters": t.schema},
            }
            for t in tools
        ]
        calls = 0
        usage = {"input_tokens": 0, "output_tokens": 0}
        stop = "max_turns"
        final_text = ""
        for _ in range(max_turns):
            response = self.client.chat.completions.create(
                model=self.model, messages=messages, tools=tool_defs
            )
            if response.usage:
                usage["input_tokens"] += response.usage.prompt_tokens or 0
                usage["output_tokens"] += response.usage.completion_tokens or 0
            choice = response.choices[0]
            msg = choice.message
            stop = choice.finish_reason or "unknown"
            final_text = msg.content or ""
            messages.append(msg.model_dump(exclude_none=True))
            if not msg.tool_calls:
                break
            for call in msg.tool_calls:
                calls += 1
                try:
                    args = json.loads(call.function.arguments or "{}")
                    out = handler(call.function.name, args)
                except (ToolError, json.JSONDecodeError) as e:
                    out = f"Error: {e}"
                messages.append({"role": "tool", "tool_call_id": call.id, "content": out})
            if should_stop():
                stop = "submitted"
                break
        return RunResult(final_text=final_text, tool_calls=calls, stop_reason=stop, usage=usage)

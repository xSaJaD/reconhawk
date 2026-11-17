from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from openai import AsyncOpenAI


# Vendor-only knobs are enabled only for the native API, not for a model name
# that happens to look like OpenAI on an arbitrary compatible gateway.
_REASONING_MODEL = re.compile(r"^(?:gpt-5(?:[.-]|$)|o[134](?:-|$))")


def _native_openai(provider: str, base_url: str) -> bool:
    try:
        url = urlsplit(base_url)
        return (provider == "openai" and url.scheme == "https"
                and url.hostname == "api.openai.com" and url.port in (None, 443)
                and url.path.rstrip("/") == "/v1" and not url.username and not url.password)
    except ValueError:
        return False


def _to_openai_tools(tools: list[dict]) -> list[dict]:
    """Convert neutral (Anthropic-shaped) tools to OpenAI function tools."""
    out = []
    for t in tools:
        out.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return out


class OpenAICompatLLM:
    """Works with OpenAI, OpenRouter, Groq, Ollama, LM Studio — anything speaking the
    OpenAI Chat Completions API with tool calling."""

    def __init__(self, model: str, api_key: str | None, base_url: str,
                 provider: str = "custom", effort: str = "high"):
        self.model = model
        self.provider = provider
        self.effort = effort
        self.native_openai = _native_openai(provider, base_url)
        self.reasoning_tokens = self.native_openai and bool(_REASONING_MODEL.match(model))
        self.reasoning_effort = self.reasoning_tokens and not any(
            marker in model for marker in ("-chat", "-codex", "-realtime", "-audio", "-deep-research")
        ) and not model.startswith(("o1-preview", "o1-mini"))
        # Some local gateways need a non-empty key string.
        self.client = AsyncOpenAI(api_key=api_key or "not-needed", base_url=base_url)

    def _request_options(self, tokens: int, effort: str | None = None) -> dict:
        options = {"max_completion_tokens" if self.reasoning_tokens else "max_tokens": tokens}
        if self.reasoning_effort:
            requested = effort or self.effort
            if requested not in ("low", "medium", "high", "xhigh", "max"):
                raise ValueError(f"Unsupported reasoning effort: {requested}")
            # low/medium/high are the common native reasoning contract. Higher
            # Anthropic/UI values are explicitly mapped, not sent speculatively.
            options["reasoning_effort"] = {"xhigh": "high", "max": "high"}.get(requested, requested)
        return options

    def _effort_notice(self, requested: str) -> str:
        if not self.reasoning_effort:
            return (f"Effort '{requested}' is not forwarded for {self.provider}/{self.model}; "
                    "this adapter uses the standard compatible Chat Completions parameters.")
        mapped = self._request_options(1, requested)["reasoning_effort"]
        if mapped != requested:
            return f"Reasoning effort '{requested}' maps to '{mapped}' for {self.provider}/{self.model}."
        return ""

    async def complete(self, system: str, user: str, on_usage=None) -> str:
        resp = await self.client.chat.completions.create(
            model=self.model, **self._request_options(4000),
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        if on_usage and resp.usage: await on_usage(resp.usage.prompt_tokens,resp.usage.completion_tokens)
        return resp.choices[0].message.content or ""

    async def agent_loop(self, *, system, kickoff, tools, dispatch, emit, should_stop,
                         max_iterations, on_usage=None, inbox=None, get_effort=None, initial_messages=None, on_checkpoint=None):
        oai_tools = _to_openai_tools(tools)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": kickoff},
        ]
        if initial_messages:
            messages=[m for m in initial_messages if m.get('role')!='system']
            messages.insert(0,{'role':'system','content':system})
            messages.append({'role':'user','content':'Resume with current policy and persisted context. '+kickoff})
        final_text = ""
        self.last_stop_reason = 'iteration_limit'
        previous_effort = None
        for _ in range(max_iterations):
            if should_stop():
                self.last_stop_reason = 'stopped'
                break
            if inbox:
                for msg in inbox():
                    messages.append({"role": "user", "content": f"[OPERATOR] {msg}"})
                    await emit("log", "Operator message received", {"text": msg})
            requested_effort = (get_effort() if get_effort else self.effort) or self.effort
            if requested_effort != previous_effort:
                notice = self._effort_notice(requested_effort)
                if notice:
                    await emit("log", "Model capability", {"text": notice})
                previous_effort = requested_effort
            resp = await self.client.chat.completions.create(
                model=self.model, messages=messages, tools=oai_tools,
                tool_choice="auto", **self._request_options(8000, requested_effort),
            )
            if on_usage and getattr(resp, "usage", None):
                await on_usage(getattr(resp.usage, "prompt_tokens", 0) or 0,
                               getattr(resp.usage, "completion_tokens", 0) or 0)
            msg = resp.choices[0].message

            if msg.content and msg.content.strip():
                final_text = msg.content
                await emit("log", "", {"text": msg.content})

            tool_calls = msg.tool_calls or []
            # Append the assistant turn exactly as returned.
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in tool_calls
                ] if tool_calls else None,
            })

            if not tool_calls:
                self.last_stop_reason = 'model_finished' if getattr(resp.choices[0], 'finish_reason', 'stop') == 'stop' else str(resp.choices[0].finish_reason)
                if on_checkpoint: await on_checkpoint(messages)
                break

            for tc in tool_calls:
                if should_stop():
                    messages.append({'role':'tool','tool_call_id':tc.id,'content':'Skipped: task paused'})
                    continue
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                await emit("tool_call", tc.function.name, {"tool": tc.function.name, "input": args})
                out, is_err = await dispatch(tc.function.name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": (("ERROR: " + out) if is_err else out),
                })
            if on_checkpoint: await on_checkpoint(messages)
        return final_text

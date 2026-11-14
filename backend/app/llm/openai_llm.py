from __future__ import annotations

import json

from openai import AsyncOpenAI


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

    def __init__(self, model: str, api_key: str | None, base_url: str):
        self.model = model
        # Some local gateways need a non-empty key string.
        self.client = AsyncOpenAI(api_key=api_key or "not-needed", base_url=base_url)

    async def complete(self, system: str, user: str, on_usage=None) -> str:
        resp = await self.client.chat.completions.create(
            model=self.model, max_tokens=4000,
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
        for _ in range(max_iterations):
            if should_stop():
                break
            if inbox:
                for msg in inbox():
                    messages.append({"role": "user", "content": f"[OPERATOR] {msg}"})
                    await emit("log", "Operator message received", {"text": msg})
            resp = await self.client.chat.completions.create(
                model=self.model, messages=messages, tools=oai_tools,
                tool_choice="auto", max_tokens=8000,
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

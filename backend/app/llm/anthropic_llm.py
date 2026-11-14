from __future__ import annotations

from anthropic import AsyncAnthropic


class AnthropicLLM:
    def __init__(self, model: str, api_key: str | None, effort: str = "high"):
        self.model = model
        self.effort = effort
        self.client = AsyncAnthropic(api_key=api_key)

    async def complete(self, system: str, user: str) -> str:
        resp = await self.client.messages.create(
            model=self.model, max_tokens=4000, system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in resp.content if b.type == "text")

    async def agent_loop(self, *, system, kickoff, tools, dispatch, emit, should_stop,
                         max_iterations, on_usage=None, inbox=None, get_effort=None):
        messages = [{"role": "user", "content": kickoff}]
        final_text = ""
        for _ in range(max_iterations):
            if should_stop():
                break
            # Drain any operator messages queued into the running agent.
            if inbox:
                for msg in inbox():
                    messages.append({"role": "user", "content": f"[OPERATOR] {msg}"})
                    await emit("log", "Operator message received", {"text": msg})
            kwargs = dict(
                model=self.model, max_tokens=16000, system=system,
                tools=tools, messages=messages,
            )
            # Adaptive thinking + effort are Anthropic-specific; harmless defaults.
            kwargs["thinking"] = {"type": "adaptive"}
            kwargs["output_config"] = {"effort": (get_effort() if get_effort else self.effort)}
            try:
                resp = await self.client.messages.create(**kwargs)
            except Exception:
                # Retry once without the newer knobs for older models / gateways.
                kwargs.pop("thinking", None)
                kwargs.pop("output_config", None)
                resp = await self.client.messages.create(**kwargs)

            if on_usage and getattr(resp, "usage", None):
                await on_usage(getattr(resp.usage, "input_tokens", 0) or 0,
                               getattr(resp.usage, "output_tokens", 0) or 0)

            for block in resp.content:
                if block.type == "text" and block.text.strip():
                    final_text = block.text
                    await emit("log", "", {"text": block.text})

            if resp.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": resp.content})
                continue

            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            if resp.stop_reason == "end_turn" or not tool_uses:
                break

            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for tu in tool_uses:
                await emit("tool_call", tu.name, {"tool": tu.name, "input": tu.input})
                out, is_err = await dispatch(tu.name, tu.input or {})
                results.append({
                    "type": "tool_result", "tool_use_id": tu.id,
                    "content": out, "is_error": is_err,
                })
            messages.append({"role": "user", "content": results})
        return final_text

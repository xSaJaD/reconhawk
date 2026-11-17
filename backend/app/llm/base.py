from __future__ import annotations

from typing import Awaitable, Callable, Protocol

# Neutral tool spec = Anthropic shape: {name, description, input_schema}
Tool = dict
# dispatch(name, args) -> (result_text, is_error)
Dispatch = Callable[[str, dict], Awaitable[tuple[str, bool]]]
# emit(kind, title, body) -> None   (kind: "log" | "tool_call")
Emit = Callable[[str, str, object], Awaitable[None]]
# should_stop() -> bool
ShouldStop = Callable[[], bool]


class LLM(Protocol):
    async def agent_loop(
        self, *, system: str, kickoff: str, tools: list[Tool],
        dispatch: Dispatch, emit: Emit, should_stop: ShouldStop, max_iterations: int,
        on_usage=None, inbox=None, get_effort=None, initial_messages=None, on_checkpoint=None,
    ) -> str: ...

    async def complete(self, system: str, user: str, on_usage=None) -> str: ...


# Provider catalog for the UI model picker. Models are suggestions; any string is allowed.
PROVIDERS = {
    "anthropic": {
        "label": "Anthropic (Claude)",
        "base_url_editable": False,
        "models": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-4-8"],
        "key_setting": "anthropic_api_key",
    },
    "openai": {
        "label": "OpenAI",
        "base_url_editable": True,
        "models": ["gpt-5", "gpt-5-mini", "gpt-4.1", "gpt-4o", "o4-mini"],
        "key_setting": "openai_api_key",
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url_editable": False,
        "models": [
            "anthropic/claude-opus-4.1", "openai/gpt-4o", "google/gemini-2.5-pro",
            "deepseek/deepseek-chat", "meta-llama/llama-3.3-70b-instruct",
        ],
        "key_setting": "openrouter_api_key",
    },
    "custom": {
        "label": "Custom (OpenAI-compatible: Ollama, Groq, LM Studio…)",
        "base_url_editable": True,
        "models": ["llama3.1", "qwen2.5", "mistral"],
        "key_setting": "custom_api_key",
    },
}


def build_llm(cfg: dict, provider: str | None = None, model: str | None = None) -> "LLM":
    """cfg comes from settings_store.llm_config(). provider/model override the stored defaults."""
    provider = (provider or cfg["provider"] or "anthropic").lower()
    model = model or cfg["model"]
    effort = cfg.get("effort", "high")

    if provider == "anthropic":
        from .anthropic_llm import AnthropicLLM
        return AnthropicLLM(model=model or "claude-opus-5-5",
                            api_key=cfg.get("anthropic_api_key") or None, effort=effort)

    if provider not in ("openai", "openrouter", "custom"):
        raise ValueError(f"Unsupported provider: {provider}")
    # Each compatible provider owns its endpoint and credentials.
    from .openai_llm import OpenAICompatLLM
    if provider == "openrouter":
        base_url = "https://openrouter.ai/api/v1"
        api_key = cfg.get("openrouter_api_key") or None
    elif provider == "custom":
        # A fully independent custom endpoint (any OpenAI-compatible API).
        base_url = cfg.get("custom_base_url") or "http://localhost:11434/v1"
        api_key = cfg.get("custom_api_key") or None
    else:  # openai
        base_url = cfg.get("openai_base_url") or "https://api.openai.com/v1"
        api_key = cfg.get("openai_api_key") or None
    return OpenAICompatLLM(model=model or "gpt-4o", api_key=api_key, base_url=base_url,
                           provider=provider, effort=effort)

from __future__ import annotations

from sqlalchemy import select
import json

from .config import settings as env
from .db import SessionLocal, Setting

# Keys that hold secrets (masked when read by the UI).
SECRET_KEYS = {"anthropic_api_key", "openai_api_key", "openrouter_api_key",
               "custom_api_key", "telegram_bot_token", "hackerone_token"}

# Defaults sourced from env on first run.
DEFAULTS = {
    "llm_provider": "anthropic",
    "llm_model": env.agent_model,
    "llm_effort": env.agent_effort,
    "agent_max_iterations": str(env.agent_max_iterations),
    "anthropic_api_key": env.anthropic_api_key,
    "openai_api_key": "",
    "openai_base_url": "https://api.openai.com/v1",
    "openrouter_api_key": "",
    # fully custom OpenAI-compatible endpoint (any provider you want)
    "custom_base_url": "",
    "custom_api_key": "",
    "scope_enforced": "true" if env.scope_enforced else "false",
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "hackerone_username": "",
    "hackerone_token": "",
    # HackerOne scope sync: start in the background when the service starts, refresh only
    # stale programs, and fetch several programs at once within the API rate limit.
    "h1_auto_sync": "true",
    "h1_sync_concurrency": "6",
    "h1_requests_per_second": "8",
    "h1_scope_ttl_hours": "12",
    "verify_threshold": "80",
    "auto_verify": "true",
    "auto_telegram": "true",
    "auto_submit": "false",
    "enable_shell": "false",
    # Cost-aware routing: a cheaper "worker" model for light steps (verification, summaries).
    # Empty = use the main model. Provider reuses the main provider unless set.
    "worker_provider": "",
    "worker_model": "",
    # budget / limits (0 = unlimited)
    "budget_usd_per_task": "0",
    "max_subagents": "4",
    "subagent_max_iterations": "14",
    "max_steps_per_task": "200",
    "enable_mcp": "false",
    "mcp_servers": "[]",
    "accent_color": "#39d98a",
    "max_passes": "3",  # continuation passes while open leads/ideas remain
    # authenticated testing — account provisioning is OFF by default and hard-capped
    "allow_account_provisioning": "false",
    "max_test_accounts": "2",
}


async def get_all(mask: bool = False) -> dict:
    out = dict(DEFAULTS)
    async with SessionLocal() as s:
        rows = (await s.execute(select(Setting))).scalars().all()
        for r in rows:
            out[r.key] = r.value
    if mask:
        for k in SECRET_KEYS:
            if out.get(k):
                out[k] = "••••••" + out[k][-4:] if len(out[k]) > 4 else "••••••"
    return out


async def get(key: str, default: str = "") -> str:
    async with SessionLocal() as s:
        row = await s.get(Setting, key)
        if row is not None:
            return row.value
    return DEFAULTS.get(key, default)


async def set_many(values: dict) -> None:
    limits={'agent_max_iterations':(1,500),'max_steps_per_task':(0,10000),'max_subagents':(0,8),'subagent_max_iterations':(1,100),'max_passes':(1,20),'max_test_accounts':(0,2),'budget_usd_per_task':(0,100000),'verify_threshold':(1,100),'h1_sync_concurrency':(1,16),'h1_requests_per_second':(1,10),'h1_scope_ttl_hours':(0,168)}
    for key,value in values.items():
        if key not in DEFAULTS: raise ValueError('Unknown setting: '+key)
        if key in limits:
            number=float(value) if key=='budget_usd_per_task' else int(value)
            if not limits[key][0]<=number<=limits[key][1]: raise ValueError('Invalid limit: '+key)
        if DEFAULTS[key] in ('true','false') and str(value) not in ('true','false'): raise ValueError('Invalid boolean')
        if key in ('llm_provider', 'worker_provider') and value not in ('anthropic', 'openai', 'openrouter', 'custom'):
            if not (key == 'worker_provider' and value == ''):
                raise ValueError('Unsupported provider')
        if key=='mcp_servers':
            from .mcp_client import validate
            validate(json.loads(value))
    async with SessionLocal() as s:
        for k, v in values.items():
            # Skip masked secret values the UI echoes back unchanged.
            if k in SECRET_KEYS and isinstance(v, str) and v.startswith("••••••"):
                continue
            row = await s.get(Setting, k)
            if row is None:
                s.add(Setting(key=k, value=str(v)))
            else:
                row.value = str(v)
        await s.commit()
    if 'scope_enforced' in values: env.scope_enforced=str(values['scope_enforced'])=='true'


async def provider_has_key(provider: str | None = None) -> bool:
    """Whether the selected provider has the credential it needs.
    'custom' (local OpenAI-compatible servers like Ollama) needs no key."""
    cfg = await get_all()
    provider = provider or cfg["llm_provider"]
    if provider == "custom":
        return True
    key_field = {
        "anthropic": "anthropic_api_key",
        "openai": "openai_api_key",
        "openrouter": "openrouter_api_key",
    }.get(provider, "")
    return bool(cfg.get(key_field))


async def llm_config() -> dict:
    cfg = await get_all()
    return {
        "provider": cfg["llm_provider"],
        "model": cfg["llm_model"],
        "effort": cfg["llm_effort"],
        "max_iterations": int(cfg.get("agent_max_iterations") or 40),
        "anthropic_api_key": cfg["anthropic_api_key"],
        "openai_api_key": cfg["openai_api_key"],
        "openai_base_url": cfg["openai_base_url"],
        "openrouter_api_key": cfg["openrouter_api_key"],
        "custom_base_url": cfg.get("custom_base_url", ""),
        "custom_api_key": cfg.get("custom_api_key", ""),
        # worker (cheap) routing — falls back to main when unset
        "worker_provider": cfg.get("worker_provider") or cfg["llm_provider"],
        "worker_model": cfg.get("worker_model") or cfg["llm_model"],
    }

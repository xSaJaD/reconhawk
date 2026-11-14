from __future__ import annotations

# ($/1M input, $/1M output). Only well-known models; everything else => None (show tokens only).
# These are list prices for estimation; never presented as billed amounts.
PRICING = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-2": (8.0, 40.0),
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.0, 8.0),
    "o4-mini": (1.1, 4.4),
}


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float | None:
    """USD estimate, or None when the model's price is unknown (custom/local)."""
    key = (model or "").lower()
    price = PRICING.get(key)
    if price is None:
        # try a prefix match (e.g. provider/model on openrouter)
        for k, v in sorted(PRICING.items(), key=lambda item: -len(item[0])):
            if key.rsplit('/',1)[-1] == k or key.rsplit('/',1)[-1].startswith(k + '-'):
                price = v
                break
    if price is None:
        return None
    return tokens_in / 1_000_000 * price[0] + tokens_out / 1_000_000 * price[1]

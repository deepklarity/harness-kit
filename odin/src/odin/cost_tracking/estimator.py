"""Cost estimation from token counts and model pricing."""

import json
from pathlib import Path
from typing import Dict, Optional, Tuple

# Model name → (input_price_per_1m_tokens, output_price_per_1m_tokens)
PricingTable = Dict[str, Tuple[Optional[float], Optional[float]]]


def load_pricing_table(agent_models_path: str) -> PricingTable:
    """Load pricing data from agent_models.json into a flat lookup dict."""
    data = json.loads(Path(agent_models_path).read_text())
    table: PricingTable = {}
    for agent_info in data.get("agents", {}).values():
        for model in agent_info.get("models", []):
            name = model["name"]
            table[name] = (
                model.get("input_price_per_1m_tokens"),
                model.get("output_price_per_1m_tokens"),
            )
    return table


def estimate_cost(
    model: str,
    input_tokens: Optional[int],
    output_tokens: Optional[int],
    pricing: PricingTable,
) -> Optional[float]:
    """Estimate cost in USD for a single invocation.

    Returns None if:
    - Model not in pricing table
    - Model has null pricing (unknown cost)
    - Token counts are None (can't estimate)
    """
    if input_tokens is None or output_tokens is None:
        return None

    if model not in pricing:
        return None

    input_price, output_price = pricing[model]
    if input_price is None or output_price is None:
        return None

    return (input_tokens / 1_000_000) * input_price + (output_tokens / 1_000_000) * output_price

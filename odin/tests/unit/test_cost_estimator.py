"""Tests for cost estimation — pricing table, estimator, task summarization.

Tags: [simple] — Pure logic, no I/O (uses tmp_path for pricing table file).
"""

import json
from pathlib import Path

import pytest

from odin.cost_tracking.estimator import estimate_cost, load_pricing_table


# ── Pricing table loading ────────────────────────────────────────────


class TestLoadPricingTable:
    def test_loads_all_models(self, tmp_path):
        """All 23 models in agent_models.json should have entries."""
        agent_models_path = Path(__file__).resolve().parents[3] / "taskit" / "taskit-backend" / "data" / "agent_models.json"
        if not agent_models_path.exists():
            pytest.skip("agent_models.json not found")

        table = load_pricing_table(str(agent_models_path))
        # Count all models from the JSON
        with open(agent_models_path) as f:
            data = json.load(f)
        expected_count = sum(len(agent["models"]) for agent in data["agents"].values())
        assert len(table) == expected_count

    def test_known_model_has_prices(self, tmp_path):
        """Models with known pricing should have non-None values."""
        agent_models_path = Path(__file__).resolve().parents[3] / "taskit" / "taskit-backend" / "data" / "agent_models.json"
        if not agent_models_path.exists():
            pytest.skip("agent_models.json not found")

        table = load_pricing_table(str(agent_models_path))
        input_price, output_price = table["claude-sonnet-4-5"]
        assert input_price == 3.00
        assert output_price == 15.00

    def test_unknown_model_has_none_prices(self, tmp_path):
        """Models with null pricing should have None values."""
        agent_models_path = Path(__file__).resolve().parents[3] / "taskit" / "taskit-backend" / "data" / "agent_models.json"
        if not agent_models_path.exists():
            pytest.skip("agent_models.json not found")

        table = load_pricing_table(str(agent_models_path))
        input_price, output_price = table["qwen3-coder"]
        assert input_price is None
        assert output_price is None

    def test_loads_from_minimal_json(self, tmp_path):
        """Verify loading from a custom JSON file."""
        data = {
            "agents": {
                "test": {
                    "color": "#000",
                    "models": [
                        {"name": "test-model", "input_price_per_1m_tokens": 1.0, "output_price_per_1m_tokens": 2.0}
                    ]
                }
            }
        }
        path = tmp_path / "models.json"
        path.write_text(json.dumps(data))

        table = load_pricing_table(str(path))
        assert table["test-model"] == (1.0, 2.0)


# ── Cost estimation ──────────────────────────────────────────────────


class TestEstimateCost:
    @pytest.fixture
    def pricing(self):
        return {
            "claude-sonnet-4-5": (3.00, 15.00),
            "qwen3-coder": (None, None),
            "gemini-2.5-flash": (0.15, 0.60),
        }

    def test_known_model(self, pricing):
        """claude-sonnet-4-5, 1000 in / 500 out → $0.0105."""
        cost = estimate_cost("claude-sonnet-4-5", 1000, 500, pricing)
        # (1000 / 1_000_000) * 3.00 + (500 / 1_000_000) * 15.00
        # = 0.003 + 0.0075 = 0.0105
        assert cost == pytest.approx(0.0105)

    def test_unknown_model(self, pricing):
        """Model with null pricing returns None."""
        cost = estimate_cost("qwen3-coder", 1000, 500, pricing)
        assert cost is None

    def test_missing_model(self, pricing):
        """Model not in pricing table returns None."""
        cost = estimate_cost("nonexistent-model", 1000, 500, pricing)
        assert cost is None

    def test_zero_tokens(self, pricing):
        """Zero tokens → $0.00."""
        cost = estimate_cost("claude-sonnet-4-5", 0, 0, pricing)
        assert cost == 0.0

    def test_null_tokens(self, pricing):
        """None tokens → None (can't estimate)."""
        cost = estimate_cost("claude-sonnet-4-5", None, None, pricing)
        assert cost is None

    def test_partial_null_tokens(self, pricing):
        """One token count None → None."""
        cost = estimate_cost("claude-sonnet-4-5", 1000, None, pricing)
        assert cost is None

    def test_large_token_count(self, pricing):
        """Verify with a realistic large token count."""
        # 100k input, 50k output on gemini-2.5-flash
        cost = estimate_cost("gemini-2.5-flash", 100_000, 50_000, pricing)
        # (100000 / 1M) * 0.15 + (50000 / 1M) * 0.60 = 0.015 + 0.030 = 0.045
        assert cost == pytest.approx(0.045)

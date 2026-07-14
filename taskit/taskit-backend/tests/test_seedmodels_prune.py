"""Tests for `seedmodels` model-list pruning.

agent_models.json is the single source of truth for each agent's available
models. When a model is removed from the JSON (e.g. curating down to
latest-per-class), the default (prune) mode of `seedmodels` must drop it
from any existing User.available_models — otherwise a retired model name
keeps leaking into routing/UI even though it no longer exists in the seed
file.
"""

import json
import tempfile
from pathlib import Path

from django.core.management import call_command

from .base import APITestCase
from tasks.models import User


def _write_catalog(path, models, agent_name="claude"):
    catalog = {
        "agents": {
            agent_name: {
                "color": "#8b5cf6",
                "cost_tier": "high",
                "capabilities": ["coding"],
                "cli_command": agent_name,
                "default_model": models[0]["name"],
                "premium_model": models[0]["name"],
                "models": models,
            }
        }
    }
    path.write_text(json.dumps(catalog))


class TestSeedmodelsPrune(APITestCase):
    """Prune mode (default) must remove stale models from available_models."""

    def _seed(self, models):
        with tempfile.TemporaryDirectory() as tmp:
            catalog_path = Path(tmp) / "agent_models.json"
            _write_catalog(catalog_path, models)
            call_command("seedmodels", file=str(catalog_path))

    def test_prune_removes_model_dropped_from_json(self):
        """Seeding with 3 models then re-seeding with 1 drops the other 2."""
        self._seed([
            {"name": "model-a", "is_default": True, "input_price_per_1m_tokens": 1.0,
             "output_price_per_1m_tokens": 2.0, "cache_read_price_per_1m_tokens": 0.1},
            {"name": "model-b", "is_default": False, "input_price_per_1m_tokens": 1.0,
             "output_price_per_1m_tokens": 2.0, "cache_read_price_per_1m_tokens": 0.1},
            {"name": "model-c", "is_default": False, "input_price_per_1m_tokens": 1.0,
             "output_price_per_1m_tokens": 2.0, "cache_read_price_per_1m_tokens": 0.1},
        ])
        user = User.objects.get(email="claude@odin.agent")
        names = {m["name"] for m in user.available_models}
        self.assertEqual(names, {"model-a", "model-b", "model-c"})

        # Re-seed with only model-a — b and c must be pruned.
        self._seed([
            {"name": "model-a", "is_default": True, "input_price_per_1m_tokens": 1.0,
             "output_price_per_1m_tokens": 2.0, "cache_read_price_per_1m_tokens": 0.1},
        ])
        user.refresh_from_db()
        names = {m["name"] for m in user.available_models}
        self.assertEqual(names, {"model-a"})

    def test_no_prune_flag_preserves_removed_models(self):
        """--no-prune keeps models that were removed from the JSON (merge mode)."""
        self._seed([
            {"name": "model-a", "is_default": True, "input_price_per_1m_tokens": 1.0,
             "output_price_per_1m_tokens": 2.0, "cache_read_price_per_1m_tokens": 0.1},
            {"name": "model-b", "is_default": False, "input_price_per_1m_tokens": 1.0,
             "output_price_per_1m_tokens": 2.0, "cache_read_price_per_1m_tokens": 0.1},
        ])

        with tempfile.TemporaryDirectory() as tmp:
            catalog_path = Path(tmp) / "agent_models.json"
            _write_catalog(catalog_path, [
                {"name": "model-a", "is_default": True, "input_price_per_1m_tokens": 1.0,
                 "output_price_per_1m_tokens": 2.0, "cache_read_price_per_1m_tokens": 0.1},
            ])
            call_command("seedmodels", file=str(catalog_path), no_prune=True)

        user = User.objects.get(email="claude@odin.agent")
        names = {m["name"] for m in user.available_models}
        self.assertEqual(names, {"model-a", "model-b"})

    def test_retired_agent_entries_are_never_seeded_or_pruned_away(self):
        """Agents flagged retired stay out of active seeding but existing
        historical User rows are deactivated, not deleted (FK integrity)."""
        with tempfile.TemporaryDirectory() as tmp:
            catalog_path = Path(tmp) / "agent_models.json"
            catalog = {
                "agents": {
                    "claude": {
                        "color": "#8b5cf6", "cost_tier": "high", "capabilities": [],
                        "cli_command": "claude", "default_model": "model-a",
                        "premium_model": "model-a",
                        "models": [{
                            "name": "model-a", "is_default": True,
                            "input_price_per_1m_tokens": 1.0,
                            "output_price_per_1m_tokens": 2.0,
                            "cache_read_price_per_1m_tokens": 0.1,
                        }],
                    },
                    "retiredagent": {
                        "color": "#000000", "cost_tier": "low", "capabilities": [],
                        "cli_command": "retiredagent", "default_model": "old-model",
                        "premium_model": "old-model", "retired": True,
                        "models": [{
                            "name": "old-model", "is_default": True, "retired": True,
                            "input_price_per_1m_tokens": 0.1,
                            "output_price_per_1m_tokens": 0.2,
                            "cache_read_price_per_1m_tokens": 0.01,
                        }],
                    },
                }
            }
            catalog_path.write_text(json.dumps(catalog))

            # Pre-existing active User for the retired agent (simulates a
            # provider that used to be active before being retired).
            User.objects.create(
                email="retiredagent@odin.agent", name="retiredagent",
                role="AGENT", is_active=True,
            )

            call_command("seedmodels", file=str(catalog_path))

        retired_user = User.objects.get(email="retiredagent@odin.agent")
        self.assertFalse(retired_user.is_active)
        self.assertTrue(User.objects.filter(email="claude@odin.agent", is_active=True).exists())

import json
import logging
from pathlib import Path

from django.core.management.base import BaseCommand

from tasks.models import User

logger = logging.getLogger(__name__)

DEFAULT_FILE = Path(__file__).resolve().parent.parent.parent.parent / "data" / "agent_models.json"


class Command(BaseCommand):
    help = "Seed agent users with their available models from a JSON catalog"

    def add_arguments(self, parser):
        parser.add_argument(
            "--file", type=str, default=str(DEFAULT_FILE),
            help="Path to agent_models.json (default: data/agent_models.json)",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Print what would be done without making changes",
        )

    def handle(self, *args, **options):
        filepath = Path(options["file"])
        dry_run = options["dry_run"]

        with open(filepath) as f:
            data = json.load(f)

        agents = data.get("agents", {})
        if not agents:
            self.stderr.write("No agents found in seed file.")
            return

        for agent_name, agent_data in agents.items():
            email = f"{agent_name}@odin.agent"
            color = agent_data.get("color", "#6366f1")
            new_models = agent_data.get("models", [])

            if dry_run:
                self.stdout.write(f"[DRY RUN] Would create/update {email} with {len(new_models)} models")
                continue

            user, created = User.objects.get_or_create(
                email=email,
                defaults={"name": agent_name, "color": color},
            )

            if not created and user.color != color:
                user.color = color

            # Ensure role is AGENT (can't rely on save() auto-detection
            # because the role field defaults to HUMAN, making the check falsy)
            user.role = "AGENT"

            # Populate agent-level metadata from seed file
            user.cost_tier = agent_data.get("cost_tier", "medium")
            user.capabilities = agent_data.get("capabilities", [])
            user.cli_command = agent_data.get("cli_command")
            user.default_model = agent_data.get("default_model")
            user.premium_model = agent_data.get("premium_model")

            # Merge models by name — add new models, update existing with new fields
            existing_by_name = {}
            for m in user.available_models:
                if isinstance(m, dict) and "name" in m:
                    existing_by_name[m["name"]] = m

            merged = []
            added = 0
            updated = 0
            for model in new_models:
                name = model["name"]
                if name in existing_by_name:
                    # Merge new fields into existing model (e.g. pricing)
                    existing = existing_by_name.pop(name)
                    changed = False
                    for key, value in model.items():
                        if key not in existing or existing[key] != value:
                            existing[key] = value
                            changed = True
                    merged.append(existing)
                    if changed:
                        updated += 1
                else:
                    merged.append(model)
                    added += 1

            # Keep any user-added models not in the seed file
            for leftover in existing_by_name.values():
                merged.append(leftover)

            user.available_models = merged
            user.save()

            verb = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(
                f"{verb} {email} — {len(merged)} models total ({added} new, {updated} updated)"
            ))

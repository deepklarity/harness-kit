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
        parser.add_argument(
            "--no-prune", action="store_true",
            help=(
                "Merge instead of prune: preserve user-added models and retired "
                "agent Users not in the JSON. Default (task #135) is to make "
                "agent_models.json authoritative — models not listed are "
                "dropped from the active agent's available_models, and agent "
                "Users not in the JSON are deactivated (is_active=False). "
                "User records are preserved (FK integrity for historical tasks)."
            ),
        )

    def handle(self, *args, **options):
        filepath = Path(options["file"])
        dry_run = options["dry_run"]
        prune = not options["no_prune"]

        with open(filepath) as f:
            data = json.load(f)

        agents = data.get("agents", {})
        if not agents:
            self.stderr.write("No agents found in seed file.")
            return

        # Agents flagged "retired" stay in the JSON (so historical cost
        # lookups keep resolving their pricing) but must never be seeded or
        # reactivated as agent Users — they're excluded from seeded_emails so
        # the prune step below deactivates them like any other stale agent.
        active_agents = {
            name: info for name, info in agents.items() if not info.get("retired")
        }
        seeded_emails = {f"{name}@odin.agent" for name in active_agents.keys()}

        for agent_name, agent_data in active_agents.items():
            email = f"{agent_name}@odin.agent"
            color = agent_data.get("color", "#6366f1")
            new_models = [m for m in agent_data.get("models", []) if not m.get("retired")]

            if dry_run:
                mode = "PRUNE" if prune else "MERGE"
                self.stdout.write(f"[DRY RUN/{mode}] Would create/update {email} with {len(new_models)} models")
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

            # Mark agents listed in the seed file as active. A User that was
            # previously deactivated (retired) and is now back in the JSON
            # gets reactivated by this re-seed (task #135 acceptance).
            if not user.is_active:
                user.is_active = True

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

            removed = 0
            if prune:
                # Default (task #135): JSON is authoritative — drop anything not
                # in the seed file. Models absent here would otherwise leak
                # into routing/UI even though agent_models.json is the single
                # source of truth.
                removed = len(existing_by_name)
            else:
                # --no-prune: preserve user-added models not in the seed file.
                for leftover in existing_by_name.values():
                    merged.append(leftover)

            user.available_models = merged
            user.save()

            verb = "Created" if created else "Updated"
            suffix = f", {removed} removed" if prune and removed else ""
            self.stdout.write(self.style.SUCCESS(
                f"{verb} {email} — {len(merged)} models total ({added} new, {updated} updated{suffix})"
            ))

        # Prune mode also deactivates agents not in the JSON (e.g. retired
        # providers like qwen, gemini). We set is_active=False rather than
        # deleting the User to preserve FK references from historical
        # tasks/comments/history rows. Active-lineup queries filter on
        # is_active=True so retired agents vanish from routing/UI but
        # remain in history lookups.
        if prune:
            stale_agents = User.objects.filter(
                email__endswith="@odin.agent",
                is_active=True,
            ).exclude(email__in=seeded_emails)

            for stale in stale_agents:
                if dry_run:
                    self.stdout.write(
                        f"[DRY RUN/PRUNE] Would deactivate {stale.email} (no longer in seed file)"
                    )
                    continue
                stale.is_active = False
                stale.save(update_fields=["is_active"])
                self.stdout.write(self.style.WARNING(
                    f"Deactivated {stale.email} (no longer in seed file)"
                ))

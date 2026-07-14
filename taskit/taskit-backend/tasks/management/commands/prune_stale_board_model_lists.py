import logging

from django.core.management.base import BaseCommand

from tasks.models import Board, User, UserRole

logger = logging.getLogger(__name__)

# Board fields that hold ordered lists of {agent_name, model_name} dicts and
# need to stay in sync with the curated model registry (agent_models.json,
# applied via `seedmodels`). Entries referencing a retired agent or a model
# dropped from that agent's available_models are stale — they can never be
# selected and just clutter the UI/selection walk.
LIST_FIELDS = ["model_escalation_priority", "reviewer_order"]


class Command(BaseCommand):
    help = (
        "Remove stale {agent_name, model_name} entries from Board list fields "
        "(model_escalation_priority, reviewer_order) that reference an agent "
        "no longer active or a model no longer in that agent's available_models. "
        "Idempotent — safe to re-run; a clean board prints nothing removed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Print what would be removed without saving changes",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        active_agents = User.objects.filter(role=UserRole.AGENT, is_active=True)
        valid_pairs = set()
        for agent in active_agents:
            for m in agent.available_models:
                if isinstance(m, dict) and "name" in m:
                    valid_pairs.add((agent.name.lower(), m["name"]))

        # Board.agents is keyed by agent_name as used in seedmodels (User.name
        # lowercased matches the seed key, e.g. "claude", "codex"). Compare
        # case-insensitively since entries are hand-authored via the API.
        total_removed = 0
        for board in Board.objects.all():
            changed_fields = []
            for field in LIST_FIELDS:
                entries = getattr(board, field) or []
                if not entries:
                    continue

                kept, removed = [], []
                for entry in entries:
                    if not isinstance(entry, dict):
                        removed.append(entry)
                        continue
                    key = (str(entry.get("agent_name", "")).lower(), entry.get("model_name"))
                    if key in valid_pairs:
                        kept.append(entry)
                    else:
                        removed.append(entry)

                if not removed:
                    continue

                total_removed += len(removed)
                verb = "Would remove" if dry_run else "Removed"

                def _describe(entry):
                    if isinstance(entry, dict):
                        return f"{entry.get('agent_name')}/{entry.get('model_name')}"
                    return str(entry)

                described = [_describe(e) for e in removed]
                self.stdout.write(self.style.WARNING(
                    f"Board {board.id} ({board.name}): {verb} {len(removed)} stale "
                    f"{field} entr{'y' if len(removed) == 1 else 'ies'}: {described}"
                ))

                if not dry_run:
                    setattr(board, field, kept)
                    changed_fields.append(field)

            if changed_fields and not dry_run:
                board.save(update_fields=changed_fields)

        if total_removed == 0:
            self.stdout.write(self.style.SUCCESS("No stale board model-list entries found."))
        else:
            summary = "Would remove" if dry_run else "Removed"
            self.stdout.write(self.style.SUCCESS(f"{summary} {total_removed} stale entries total."))

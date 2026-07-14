# One-time ledger seed for task #246: the original "no reviewer available"
# bug in _resolve_reviewer_for_model for zai-coding-plan/glm-5.2 (and the
# zai/minimax family in general). The symptom is recorded with
# disposition=fixed and a pointer to the fix so a future regression of the
# same shape surfaces in the same bucket via
# ``testing_tools/errors.py --disposition open``.

import django.db.models.deletion
from django.db import migrations, models


def _seed_original_symptom(apps, schema_editor):
    ErrorEvent = apps.get_model("tasks", "ErrorEvent")
    ErrorEvent.objects.get_or_create(
        source="reflection_no_reviewer",
        source_id="task-246:original-symptom",
        defaults={
            "symptom": (
                "_resolve_reviewer_for_model returned (None, None) for "
                "zai-coding-plan/glm-5.2 even though glm@odin.agent advertises "
                "the model. Root cause: (1) the resolver hardcoded an exclusion "
                "of glm/minimax agents, and (2) _reflection_agent_hint_for_model "
                "returned None for the zai-coding-plan/ family. Also: bare-string "
                "available_models entries would silently AttributeError inside "
                "any(m.get('name') == ...). The reflection watchdog (W6.4) then "
                "skipped forever with no-reviewer-available, parking the task "
                "in REVIEW."
            ),
            "symptom_signature": (
                "reviewer resolver missed advertised model in glm/minimax family"
            ),
            "failure_class": "configuration",
            "disposition": "fixed",
            "disposition_note": (
                "Fixed in task #246: removed the hardcoded glm/minimax agent "
                "exclusion in _resolve_reviewer_for_model and _find_first_"
                "available_reviewer; added zai-coding-plan/ and minimax-coding-"
                "plan/ hints to _reflection_agent_hint_for_model; added "
                "_model_name() helper so bare-string available_models entries "
                "are tolerated; added a watchdog escalation guard that posts a "
                "QUESTION comment + ErrorEvent after "
                "REFLECTION_WATCHDOG_NO_REVIEWER_SKIPS_BEFORE_ESCALATION (3) "
                "consecutive no-reviewer scans, idempotent via "
                "task.metadata.reflection_watchdog_no_reviewer_escalated_at."
            ),
            "context": {
                "task_id": 246,
                "root_causes": [
                    "hardcoded agent exclusion: if name in ('glm', 'minimax'): continue",
                    "_reflection_agent_hint_for_model returns None for zai-coding-plan/",
                    "m.get('name') crashes on bare-string available_models entries",
                    "watchdog silently skipped on no-reviewer",
                ],
            },
        },
    )


def _noop_reverse(apps, schema_editor):
    # Forward-only seed; reverse deletes the marker row so re-running the
    # migration produces the same state.
    ErrorEvent = apps.get_model("tasks", "ErrorEvent")
    ErrorEvent.objects.filter(
        source="reflection_no_reviewer",
        source_id="task-246:original-symptom",
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0056_errorevent_reflection_no_reviewer'),
    ]

    operations = [
        migrations.RunPython(_seed_original_symptom, _noop_reverse),
    ]

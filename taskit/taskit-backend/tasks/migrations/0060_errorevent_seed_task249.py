# One-time ledger seed for task #249: the reflect endpoint accepted explicit
# {agent, model} params but ran a stale hardcoded default (claude-opus-4-6 —
# a model no agent advertises) because (1) ReflectionRequestSerializer
# carried default="claude-opus-4-6" and (2) short alias names (agent/model)
# were silently dropped by DRF, so the operator's request never reached the
# reviewer selection. Recorded with disposition=fixed and a pointer to the
# fix so a future regression of the same shape surfaces in this bucket.

from django.db import migrations


def _seed_original_symptom(apps, schema_editor):
    ErrorEvent = apps.get_model("tasks", "ErrorEvent")
    ErrorEvent.objects.get_or_create(
        source="reflect_param_ignored",
        source_id="task-249:original-symptom",
        defaults={
            "symptom": (
                "POST /tasks/:id/reflect/ accepted explicit {agent, model} "
                "params but dispatched claude-opus-4-6 (a model no agent "
                "advertises). Root cause: ReflectionRequestSerializer carried "
                "default=\"claude-opus-4-6\", so any caller input the serializer "
                "didn't recognize — including the short alias names agent/model "
                "that DRF silently drops — fell back to the stale default and the "
                "wrong reviewer ran. No validation tied the stored reviewer to a "
                "model any agent actually carries."
            ),
            "symptom_signature": (
                "reflect endpoint ignored explicit reviewer params and ran a stale default"
            ),
            "failure_class": "configuration",
            "disposition": "fixed",
            "disposition_note": (
                "Fixed in task #249: removed the hardcoded claude/claude-opus-4-6 "
                "serializer defaults (fields are now optional) and the stale "
                "REFLECTION_ALLOWED_AGENTS allowlist; the reflect action now reads "
                "reviewer_agent/reviewer_model (and agent/model aliases) from the "
                "raw body, validates the pair against the named agent's "
                "available_models via _resolve_explicit_reviewer (400 on unknown), "
                "and falls back to select_reviewer_by_context_size when params are "
                "omitted. A honoring log line records the dispatched reviewer."
            ),
            "context": {
                "task_id": 249,
                "root_causes": [
                    "ReflectionRequestSerializer default=\"claude-opus-4-6\"",
                    "DRF silently drops unknown alias keys (agent/model)",
                    "no validation of reviewer against available_models",
                ],
            },
        },
    )


def _noop_reverse(apps, schema_editor):
    # Forward-only seed; reverse deletes the marker row so re-running the
    # migration produces the same state.
    ErrorEvent = apps.get_model("tasks", "ErrorEvent")
    ErrorEvent.objects.filter(
        source="reflect_param_ignored",
        source_id="task-249:original-symptom",
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0059_errorevent_reflect_param_ignored'),
    ]

    operations = [
        migrations.RunPython(_seed_original_symptom, _noop_reverse),
    ]

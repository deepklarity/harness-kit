from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError
import calendar

from django.db import transaction
from django.utils import timezone

from .kanban_ordering import move_task
from .models import (
    Label, ScheduleKind, ScheduleRunStatus, ScheduleStatus, Spec, Task,
    TaskComment, TaskHistory, TaskPriority, TaskSchedule, TaskScheduleRun, TaskStatus, User,
)


TERMINAL_SUCCESS_STATUSES = {TaskStatus.DONE, TaskStatus.TESTING}
TERMINAL_FAILURE_STATUSES = {TaskStatus.FAILED}
TERMINAL_CANCELED_STATUSES = {TaskStatus.CANCELED}
ACTIVE_OVERLAP_STATUSES = {TaskStatus.IN_PROGRESS, TaskStatus.EXECUTING}
WEEKDAY_INDEX = {
    "MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6,
}


@dataclass
class ScheduleTemplate:
    title: str
    description: str = ""
    priority: str = TaskPriority.MEDIUM
    assignee_id: int | None = None
    model_name: str | None = None
    label_ids: list[int] | None = None
    depends_on: list[str] | None = None
    dev_eta_seconds: int | None = None
    spec_id: int | None = None
    metadata: dict | None = None


class ScheduleValidationError(ValueError):
    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field
        self.message = message


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except ZoneInfoNotFoundError as exc:
        raise ScheduleValidationError("timezone", "Unknown timezone. Use a valid IANA timezone name.") from exc


def _naive_local(dt: datetime, tz_name: str) -> datetime:
    del tz_name
    return dt.replace(tzinfo=None)


def _resolve_local_datetime(dt: datetime, tz_name: str, field_name: str = "starts_at_local") -> datetime:
    tz = _zone(tz_name)
    if timezone.is_aware(dt):
        return dt.astimezone(tz)

    matches: list[tuple[int, timedelta | None]] = []
    for fold in (0, 1):
        aware = dt.replace(tzinfo=tz, fold=fold)
        roundtrip = aware.astimezone(dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
        if roundtrip == dt:
            matches.append((fold, aware.utcoffset()))

    if not matches:
        raise ScheduleValidationError(field_name, "Local datetime does not exist in the selected timezone.")
    if len(matches) == 2 and matches[0][1] != matches[1][1]:
        raise ScheduleValidationError(field_name, "Local datetime is ambiguous in the selected timezone.")
    return dt.replace(tzinfo=tz, fold=matches[0][0])


def parse_local_datetime(value: str | datetime, tz_name: str) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip()
        if not raw:
            raise ScheduleValidationError("starts_at_local", "Local datetime value is required.")
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ScheduleValidationError("starts_at_local", "Invalid datetime format. Use ISO 8601.") from exc
    return _resolve_local_datetime(dt, tz_name, field_name="starts_at_local")


def preserve_local_datetime(value: str | datetime, tz_name: str) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip()
        if not raw:
            raise ScheduleValidationError("starts_at_local", "Local datetime value is required.")
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ScheduleValidationError("starts_at_local", "Invalid datetime format. Use ISO 8601.") from exc
    return _resolve_local_datetime(_naive_local(dt, tz_name), tz_name, field_name="starts_at_local")


def rebind_local_datetime(value: datetime, from_tz_name: str, to_tz_name: str) -> datetime:
    source_tz = _zone(from_tz_name)
    if timezone.is_aware(value):
        naive_local = value.astimezone(source_tz).replace(tzinfo=None)
    else:
        naive_local = value
    return _resolve_local_datetime(naive_local, to_tz_name, field_name="starts_at_local")


def local_to_utc(dt: datetime, tz_name: str) -> datetime:
    if timezone.is_naive(dt):
        localized = _resolve_local_datetime(dt, tz_name)
    else:
        localized = dt.astimezone(_zone(tz_name))
    return localized.astimezone(dt_timezone.utc)


def _copy_local(dt: datetime, year: int, month: int, day: int) -> datetime:
    return dt.replace(year=year, month=month, day=day)


def _candidate_monthdays(year: int, month: int, by_monthday: list[int]) -> list[int]:
    _, last_day = calendar.monthrange(year, month)
    if not by_monthday:
        return []
    return sorted(day for day in by_monthday if day <= last_day)


def compute_next_occurrence(starts_at_local: datetime, recurrence_rule: dict, after_utc: datetime, tz_name: str) -> datetime | None:
    tz = _zone(tz_name)
    starts_local = starts_at_local.astimezone(tz) if timezone.is_aware(starts_at_local) else _resolve_local_datetime(starts_at_local, tz_name)
    interval = int(recurrence_rule.get("interval") or 1)
    freq = recurrence_rule.get("freq")
    end_mode = recurrence_rule.get("end_mode") or "NEVER"
    until_local = recurrence_rule.get("until_local")
    if isinstance(until_local, str) and until_local:
        until_local = parse_local_datetime(until_local, tz_name)
    if until_local and timezone.is_naive(until_local):
        until_local = until_local.replace(tzinfo=tz)
    count = recurrence_rule.get("occurrence_count")
    generated = 0

    cursor = starts_local
    while generated < 1000:
        candidate_utc = cursor.astimezone(dt_timezone.utc)
        if candidate_utc > after_utc:
            if until_local and cursor > until_local:
                return None
            return candidate_utc

        generated += 1
        if end_mode == "AFTER_COUNT" and count and generated >= int(count):
            return None

        if freq == "DAILY":
            cursor = preserve_local_datetime(cursor + timedelta(days=interval), tz_name)
            continue

        if freq == "WEEKLY":
            by_weekday = recurrence_rule.get("by_weekday") or []
            days = sorted(WEEKDAY_INDEX[d] for d in by_weekday) if by_weekday else [cursor.weekday()]
            probe = cursor.date() + timedelta(days=1)
            while True:
                day_offset = (probe - starts_local.date()).days
                if day_offset >= 0 and (day_offset // 7) % interval == 0 and probe.weekday() in days:
                    cursor = preserve_local_datetime(
                        datetime(
                            probe.year,
                            probe.month,
                            probe.day,
                            starts_local.hour,
                            starts_local.minute,
                            starts_local.second,
                            starts_local.microsecond,
                        ),
                        tz_name,
                    )
                    break
                probe = probe + timedelta(days=1)
            continue

        if freq == "MONTHLY":
            by_monthday = recurrence_rule.get("by_monthday") or [starts_local.day]
            probe_month = cursor.month
            probe_year = cursor.year
            while True:
                month_delta = (probe_year - starts_local.year) * 12 + (probe_month - starts_local.month)
                if month_delta >= 0 and month_delta % interval == 0:
                    valid_days = _candidate_monthdays(probe_year, probe_month, by_monthday)
                    for day in valid_days:
                        candidate = preserve_local_datetime(
                            _copy_local(starts_local, probe_year, probe_month, day),
                            tz_name,
                        )
                        if candidate > cursor:
                            cursor = candidate
                            break
                    else:
                        candidate = None
                    if candidate is not None:
                        break
                probe_month += 1
                while probe_month > 12:
                    probe_month -= 12
                    probe_year += 1
            continue

        return None
    return None


def build_schedule_template(template_data: dict) -> ScheduleTemplate:
    return ScheduleTemplate(
        title=template_data["title"],
        description=template_data.get("description", ""),
        priority=template_data.get("priority", TaskPriority.MEDIUM),
        assignee_id=template_data.get("assignee_id"),
        model_name=template_data.get("model_name"),
        label_ids=template_data.get("label_ids", []),
        depends_on=template_data.get("depends_on", []),
        dev_eta_seconds=template_data.get("dev_eta_seconds"),
        spec_id=template_data.get("spec_id"),
        metadata=template_data.get("metadata", {}),
    )


def materialize_task_from_schedule(schedule: TaskSchedule, run: TaskScheduleRun | None, created_by: str, reuse_existing: bool = False) -> Task:
    assignee = User.objects.filter(pk=schedule.template_assignee_id).first() if schedule.template_assignee_id else None
    spec = Spec.objects.filter(pk=schedule.template_spec_id).first() if schedule.template_spec_id else None
    task = schedule.materialized_task if reuse_existing and schedule.materialized_task_id else None
    if task is None:
        task = Task.objects.create(
            board=schedule.board,
            title=schedule.template_title,
            description=schedule.template_description,
            priority=schedule.template_priority,
            status=TaskStatus.IN_PROGRESS,
            created_by=created_by,
            assignee=assignee,
            spec=spec,
            dev_eta_seconds=schedule.template_dev_eta_seconds,
            depends_on=schedule.template_depends_on or [],
            metadata=schedule.template_metadata or {},
            model_name=schedule.template_model_name,
            schedule=schedule,
            current_schedule_run=run,
        )
        task.kanban_position = move_task(task, target_status=task.status, target_index=0)
        task.save(update_fields=["kanban_position"])
    else:
        task.title = schedule.template_title
        task.description = schedule.template_description
        task.priority = schedule.template_priority
        task.assignee = assignee
        task.spec = spec
        task.dev_eta_seconds = schedule.template_dev_eta_seconds
        task.depends_on = schedule.template_depends_on or []
        task.metadata = schedule.template_metadata or {}
        task.model_name = schedule.template_model_name
        task.current_schedule_run = run
        old_status = task.status
        task.status = TaskStatus.IN_PROGRESS
        task.kanban_position = move_task(task, target_status=TaskStatus.IN_PROGRESS, target_index=0)
        task.save()
        TaskHistory.objects.create(
            task=task,
            schedule_run=run,
            field_name="status",
            old_value=old_status,
            new_value=TaskStatus.IN_PROGRESS,
            changed_by="system@taskit",
        )
    if schedule.template_label_ids:
        task.labels.set(Label.objects.filter(id__in=schedule.template_label_ids))
    return task


def _run_snapshot(schedule: TaskSchedule) -> dict:
    return {
        "title": schedule.template_title,
        "description": schedule.template_description,
        "priority": schedule.template_priority,
        "assignee_id": schedule.template_assignee_id,
        "model_name": schedule.template_model_name,
        "label_ids": schedule.template_label_ids or [],
        "depends_on": schedule.template_depends_on or [],
        "dev_eta_seconds": schedule.template_dev_eta_seconds,
        "spec_id": schedule.template_spec_id,
        "metadata": schedule.template_metadata or {},
    }


def create_schedule(*, board, kind: str, timezone_name: str, starts_at_local: datetime, template: dict, recurrence_rule: dict, created_by: str) -> TaskSchedule:
    starts_at_local = parse_local_datetime(starts_at_local, timezone_name)
    starts_at_utc = local_to_utc(starts_at_local, timezone_name)
    next_run_at_utc = starts_at_utc
    schedule = TaskSchedule.objects.create(
        board=board,
        kind=kind,
        status=ScheduleStatus.ACTIVE,
        timezone=timezone_name,
        template_title=template["title"],
        template_description=template.get("description", ""),
        template_priority=template.get("priority", TaskPriority.MEDIUM),
        template_assignee_id=template.get("assignee_id"),
        template_model_name=template.get("model_name"),
        template_label_ids=template.get("label_ids", []),
        template_depends_on=template.get("depends_on", []),
        template_dev_eta_seconds=template.get("dev_eta_seconds"),
        template_spec_id=template.get("spec_id"),
        template_metadata=template.get("metadata", {}),
        starts_at_local=starts_at_local,
        starts_at_utc=starts_at_utc,
        next_run_at_utc=next_run_at_utc,
        recurrence_rule=recurrence_rule or {},
        created_by=created_by,
    )
    return schedule


def compute_schedule_next_run(schedule: TaskSchedule, reference_utc: datetime | None = None) -> datetime | None:
    reference_utc = reference_utc or timezone.now()
    if schedule.kind == ScheduleKind.ONE_TIME:
        return schedule.starts_at_utc if schedule.starts_at_utc > reference_utc else None
    if schedule.starts_at_utc > reference_utc:
        return schedule.starts_at_utc
    return compute_next_occurrence(
        schedule.starts_at_local,
        schedule.recurrence_rule or {},
        reference_utc,
        schedule.timezone,
    )


def release_schedule_occurrence(
    schedule: TaskSchedule,
    *,
    scheduled_for_utc: datetime,
    created_by: str,
    release_reason: str,
    reuse_existing: bool,
    now: datetime,
) -> tuple[TaskScheduleRun, Task | None, str]:
    """Create/claim the TaskScheduleRun for one occurrence and materialize its task.

    Shared by the cron tick (release_due_schedules) and the manual "Run now"
    endpoint so both build identical occurrences: the same get_or_create
    idempotency on the unique (schedule, scheduled_for_utc) occurrence, the same
    overlap skip, the same materialize_task_from_schedule call, and the matching
    TaskHistory row.

    Why a helper instead of calling materialize_task_from_schedule directly from
    the view: the run lifecycle (get_or_create on the occurrence, overlap skip,
    RELEASED transition, history row) is identical for both callers. Duplicating
    it in the view would be a parallel copy of the cron path -- exactly what we
    want to avoid. Only run creation + materialization lives here; the caller
    owns cron bookkeeping (advancing next_run_at_utc, setting last_released_run,
    completing one-time schedules), so a manual run can leave the cron cadence
    untouched.

    Sets schedule.materialized_task in memory when first materialized; the
    caller is responsible for persisting the schedule. Returns a tuple
    (run, task, outcome) where outcome is:
      - "released": the run was released and its task materialized
      - "overlap": a prior occurrence's task is still active, so this run was
        marked SKIPPED_OVERLAP (task is that still-active task)
      - "already": this occurrence was already processed (released or skipped
        before); the run is returned untouched
    """
    run, created = TaskScheduleRun.objects.get_or_create(
        schedule=schedule,
        scheduled_for_utc=scheduled_for_utc,
        defaults={
            "run_number": schedule.runs.count() + 1,
            "status": ScheduleRunStatus.PENDING_RELEASE,
            "template_snapshot": _run_snapshot(schedule),
        },
    )
    if not created and run.status != ScheduleRunStatus.PENDING_RELEASE:
        return run, run.task, "already"

    task = schedule.materialized_task
    if reuse_existing and task and task.status in TERMINAL_CANCELED_STATUSES:
        # CANCELED is terminal — do NOT auto-revive on the next occurrence
        # (the operator canceled the task). Mark the run CANCELED so the
        # audit trail is unambiguous; re-enabling means canceling the
        # schedule or un-canceling the task by hand. (Carried over from
        # the pre-refactor cron path, c3b710f8.)
        run.status = ScheduleRunStatus.CANCELED
        run.result_summary = (
            f"Skipped: task {task.id} is CANCELED — schedule does not "
            "auto-revive a canceled task."
        )
        run.finished_at_utc = now
        run.save(update_fields=["status", "result_summary", "finished_at_utc"])
        return run, task, "canceled"

    if reuse_existing and task and task.status in ACTIVE_OVERLAP_STATUSES:
        run.status = ScheduleRunStatus.SKIPPED_OVERLAP
        run.result_summary = f"Skipped because task {task.id} is still active."
        run.finished_at_utc = now
        run.save(update_fields=["status", "result_summary", "finished_at_utc"])
        return run, task, "overlap"

    task = materialize_task_from_schedule(
        schedule, run, created_by=created_by, reuse_existing=reuse_existing,
    )
    if not schedule.materialized_task_id:
        schedule.materialized_task = task
    run.task = task
    run.status = ScheduleRunStatus.RELEASED
    run.released_at_utc = now
    run.release_reason = release_reason
    run.save(update_fields=["task", "status", "released_at_utc", "release_reason"])
    TaskHistory.objects.create(
        task=task,
        schedule_run=run,
        field_name="status",
        old_value="",
        new_value=TaskStatus.IN_PROGRESS,
        changed_by="system@taskit",
    )
    return run, task, "released"


def release_due_schedules(now: datetime | None = None) -> int:
    now = now or timezone.now()
    released = 0
    due_ids = list(
        TaskSchedule.objects.filter(
            status=ScheduleStatus.ACTIVE,
            next_run_at_utc__isnull=False,
            next_run_at_utc__lte=now,
        ).values_list("id", flat=True)
    )
    for schedule_id in due_ids:
        with transaction.atomic():
            schedule = TaskSchedule.objects.select_for_update().select_related("materialized_task", "board").get(id=schedule_id)
            if schedule.status != ScheduleStatus.ACTIVE or not schedule.next_run_at_utc or schedule.next_run_at_utc > now:
                continue

            run, task, outcome = release_schedule_occurrence(
                schedule,
                scheduled_for_utc=schedule.next_run_at_utc,
                created_by=schedule.created_by,
                release_reason="Released by schedule due time.",
                reuse_existing=schedule.kind == ScheduleKind.RECURRING,
                now=now,
            )
            if outcome == "already":
                continue
            if outcome == "released":
                schedule.last_released_run = run

            # Cron bookkeeping advances the cadence for both released and
            # overlap-skipped occurrences (an overlapped tick still moves on).
            if schedule.kind == ScheduleKind.ONE_TIME:
                schedule.status = ScheduleStatus.COMPLETED
                schedule.completed_at = now
                schedule.next_run_at_utc = None
            else:
                next_run = compute_next_occurrence(
                    schedule.starts_at_local,
                    schedule.recurrence_rule or {},
                    schedule.next_run_at_utc,
                    schedule.timezone,
                )
                schedule.next_run_at_utc = next_run
                if next_run is None:
                    schedule.status = ScheduleStatus.COMPLETED
                    schedule.completed_at = now
            schedule.save()
            if outcome == "released":
                released += 1
    return released


def maybe_finalize_schedule_run(task: Task, new_status: str) -> None:
    run = task.current_schedule_run
    if not run:
        return
    if run.finished_at_utc:
        return
    if new_status in TERMINAL_SUCCESS_STATUSES:
        run.status = ScheduleRunStatus.COMPLETED_SUCCESS
    elif new_status in TERMINAL_CANCELED_STATUSES:
        run.status = ScheduleRunStatus.CANCELED
    elif new_status in TERMINAL_FAILURE_STATUSES:
        run.status = ScheduleRunStatus.COMPLETED_FAILED
    else:
        return
    run.terminal_task_status = new_status
    run.finished_at_utc = timezone.now()
    run.result_summary = f"Task finished with status {new_status}."
    run.save(update_fields=["status", "terminal_task_status", "finished_at_utc", "result_summary"])

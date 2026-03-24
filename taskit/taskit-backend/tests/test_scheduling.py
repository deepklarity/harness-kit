from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from tasks.models import ReflectionReport, ScheduleRunStatus, ScheduleStatus, Task, TaskComment, TaskStatus
from tasks.serializers import BoardDetailSerializer
from tasks.serializers import CreateScheduleSerializer
from tasks.scheduling import compute_next_occurrence, create_schedule, parse_local_datetime, release_due_schedules
from tasks.views import ReflectionReportViewSet, ScheduleViewSet, TaskViewSet, _exclude_hidden_scheduled_tasks

from .base import APITestCase


class SchedulingTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board(timezone="UTC")
        self.user = self.make_user()
        self.factory = APIRequestFactory()

    def test_one_time_schedule_stays_out_of_task_list_until_due(self):
        create_schedule(
            board=self.board,
            kind="ONE_TIME",
            timezone_name="UTC",
            starts_at_local=timezone.now() + timedelta(hours=1),
            template={
                "title": "Scheduled deploy",
                "description": "Ship later",
                "priority": "HIGH",
                "assignee_id": self.user.id,
            },
            recurrence_rule={},
            created_by=self.user.email,
        )
        self.assertEqual(Task.objects.count(), 0)

    def test_due_one_time_schedule_materializes_task(self):
        create_schedule(
            board=self.board,
            kind="ONE_TIME",
            timezone_name="UTC",
            starts_at_local=timezone.now() - timedelta(minutes=1),
            template={
                "title": "Scheduled deploy",
                "description": "Ship later",
                "priority": "HIGH",
                "assignee_id": self.user.id,
            },
            recurrence_rule={},
            created_by=self.user.email,
        )

        released = release_due_schedules()
        self.assertEqual(released, 1)

        task = Task.objects.get(title="Scheduled deploy")
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        self.assertIsNotNone(task.schedule_id)
        self.assertEqual(task.schedule.status, ScheduleStatus.COMPLETED)
        self.assertEqual(task.schedule.runs.count(), 1)
        self.assertEqual(task.schedule.runs.first().status, ScheduleRunStatus.RELEASED)

    def test_weekly_recurrence_must_include_first_scheduled_weekday(self):
        starts_at_local = (timezone.now() + timedelta(days=30)).astimezone(ZoneInfo("Asia/Calcutta")).replace(
            hour=18, minute=55, second=0, microsecond=0,
        )
        wrong_weekday = "MON" if starts_at_local.weekday() != 0 else "TUE"
        serializer = CreateScheduleSerializer(data={
            "board_id": self.board.id,
            "kind": "RECURRING",
            "timezone": "Asia/Calcutta",
            "starts_at_local": starts_at_local.isoformat(),
            "created_by": self.user.email,
            "template": {
                "title": "Architecture Review",
            },
            "recurrence_rule": {
                "freq": "WEEKLY",
                "interval": 1,
                "by_weekday": [wrong_weekday],
            },
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn("recurrence_rule", serializer.errors)

    def test_monthly_recurrence_must_include_first_scheduled_day(self):
        starts_at_local = (timezone.now() + timedelta(days=45)).astimezone(ZoneInfo("Asia/Calcutta")).replace(
            hour=18, minute=55, second=0, microsecond=0,
        )
        wrong_day = 1 if starts_at_local.day != 1 else 2
        serializer = CreateScheduleSerializer(data={
            "board_id": self.board.id,
            "kind": "RECURRING",
            "timezone": "Asia/Calcutta",
            "starts_at_local": starts_at_local.isoformat(),
            "created_by": self.user.email,
            "template": {
                "title": "Architecture Review",
            },
            "recurrence_rule": {
                "freq": "MONTHLY",
                "interval": 1,
                "by_monthday": [wrong_day],
            },
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn("recurrence_rule", serializer.errors)

    def test_schedule_creation_rejects_past_start_time(self):
        serializer = CreateScheduleSerializer(data={
            "board_id": self.board.id,
            "kind": "RECURRING",
            "timezone": "UTC",
            "starts_at_local": (timezone.now() - timedelta(hours=1)).replace(microsecond=0).isoformat(),
            "created_by": self.user.email,
            "template": {
                "title": "Past recurring schedule",
            },
            "recurrence_rule": {
                "freq": "DAILY",
                "interval": 1,
            },
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn("starts_at_local", serializer.errors)

    def test_schedule_creation_rejects_invalid_timezone_name(self):
        serializer = CreateScheduleSerializer(data={
            "board_id": self.board.id,
            "kind": "RECURRING",
            "timezone": "Mars/Olympus",
            "starts_at_local": "2026-04-10T09:00",
            "created_by": self.user.email,
            "template": {
                "title": "Bad timezone",
            },
            "recurrence_rule": {
                "freq": "DAILY",
                "interval": 1,
            },
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn("timezone", serializer.errors)

    def test_schedule_creation_rejects_nonexistent_dst_time(self):
        serializer = CreateScheduleSerializer(data={
            "board_id": self.board.id,
            "kind": "RECURRING",
            "timezone": "America/New_York",
            "starts_at_local": "2026-03-08T02:30",
            "created_by": self.user.email,
            "template": {
                "title": "DST jump",
            },
            "recurrence_rule": {
                "freq": "DAILY",
                "interval": 1,
            },
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn("starts_at_local", serializer.errors)

    def test_schedule_creation_rejects_ambiguous_dst_time(self):
        serializer = CreateScheduleSerializer(data={
            "board_id": self.board.id,
            "kind": "RECURRING",
            "timezone": "America/New_York",
            "starts_at_local": "2026-11-01T01:30",
            "created_by": self.user.email,
            "template": {
                "title": "DST fallback",
            },
            "recurrence_rule": {
                "freq": "DAILY",
                "interval": 1,
            },
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn("starts_at_local", serializer.errors)

    def test_hidden_scheduled_tasks_are_excluded_from_board_and_dashboard(self):
        schedule = create_schedule(
            board=self.board,
            kind="RECURRING",
            timezone_name="UTC",
            starts_at_local=timezone.now() - timedelta(days=1),
            template={
                "title": "Hidden recurring task",
                "description": "Should stay out of board surfaces between runs",
                "priority": "HIGH",
                "assignee_id": self.user.id,
            },
            recurrence_rule={
                "freq": "DAILY",
                "interval": 1,
            },
            created_by=self.user.email,
        )

        release_due_schedules()
        task = Task.objects.get(schedule=schedule)
        task.status = TaskStatus.DONE
        task.save(update_fields=["status"])

        board_data = BoardDetailSerializer(self.board).data
        self.assertEqual(board_data["tasks"], [])
        self.assertEqual(_exclude_hidden_scheduled_tasks(Task.objects.filter(board=self.board)).count(), 0)

    def test_reflection_pass_finalizes_run_at_testing_not_review(self):
        schedule = create_schedule(
            board=self.board,
            kind="RECURRING",
            timezone_name="UTC",
            starts_at_local=timezone.now() - timedelta(hours=1),
            template={
                "title": "Reflection path task",
                "priority": "HIGH",
                "assignee_id": self.user.id,
            },
            recurrence_rule={
                "freq": "DAILY",
                "interval": 1,
            },
            created_by=self.user.email,
        )
        release_due_schedules()
        task = Task.objects.get(schedule=schedule)
        task.status = TaskStatus.REVIEW
        task.save(update_fields=["status"])
        run = task.current_schedule_run

        report = ReflectionReport.objects.create(
            task=task,
            reviewer_agent="claude",
            reviewer_model="claude-sonnet",
            requested_by=self.user.email,
            context_selections=["description"],
            status="PENDING",
        )

        request = self.factory.patch(
            f"/reflections/{report.id}/",
            {
                "status": "COMPLETED",
                "verdict": "PASS",
                "verdict_summary": "Looks good.",
            },
            format="json",
        )
        force_authenticate(request, user=self.user)
        resp = ReflectionReportViewSet.as_view({"patch": "partial_update"})(request, pk=report.id)
        self.assertEqual(resp.status_code, 200)

        run.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.TESTING)
        self.assertEqual(run.status, ScheduleRunStatus.COMPLETED_SUCCESS)
        self.assertEqual(run.terminal_task_status, TaskStatus.TESTING)

    def test_question_comments_are_scoped_to_current_schedule_run(self):
        schedule = create_schedule(
            board=self.board,
            kind="RECURRING",
            timezone_name="UTC",
            starts_at_local=timezone.now() - timedelta(hours=1),
            template={
                "title": "Question scoped task",
                "priority": "HIGH",
                "assignee_id": self.user.id,
            },
            recurrence_rule={
                "freq": "DAILY",
                "interval": 1,
            },
            created_by=self.user.email,
        )
        release_due_schedules()
        task = Task.objects.get(schedule=schedule)

        request = self.factory.post(
            f"/tasks/{task.id}/question/",
            {
                "author_email": self.user.email,
                "content": "Need clarification?",
            },
            format="json",
        )
        force_authenticate(request, user=self.user)
        resp = TaskViewSet.as_view({"post": "question"})(request, pk=task.id)
        self.assertEqual(resp.status_code, 201)

        comment = TaskComment.objects.get(task=task, comment_type="question")
        self.assertEqual(comment.schedule_run_id, task.current_schedule_run_id)

    def test_updating_active_recurring_schedule_recomputes_next_future_run(self):
        schedule = create_schedule(
            board=self.board,
            kind="RECURRING",
            timezone_name="UTC",
            starts_at_local=timezone.now() - timedelta(days=3),
            template={
                "title": "Editable recurring task",
                "priority": "HIGH",
                "assignee_id": self.user.id,
            },
            recurrence_rule={
                "freq": "DAILY",
                "interval": 1,
            },
            created_by=self.user.email,
        )

        request = self.factory.patch(
            f"/schedules/{schedule.id}/",
            {
                "starts_at_local": (timezone.now() + timedelta(days=2)).replace(microsecond=0).isoformat(),
                "timezone": "UTC",
                "recurrence_rule": {
                    "freq": "DAILY",
                    "interval": 1,
                },
            },
            format="json",
        )
        force_authenticate(request, user=self.user)
        resp = ScheduleViewSet.as_view({"patch": "partial_update"})(request, pk=schedule.id)
        self.assertEqual(resp.status_code, 200)

        schedule.refresh_from_db()
        self.assertIsNotNone(schedule.next_run_at_utc)
        self.assertGreater(schedule.next_run_at_utc, timezone.now())

    def test_updating_schedule_rejects_past_start_time(self):
        schedule = create_schedule(
            board=self.board,
            kind="RECURRING",
            timezone_name="UTC",
            starts_at_local=timezone.now() + timedelta(days=1),
            template={
                "title": "Future recurring task",
                "priority": "HIGH",
                "assignee_id": self.user.id,
            },
            recurrence_rule={
                "freq": "DAILY",
                "interval": 1,
            },
            created_by=self.user.email,
        )

        request = self.factory.patch(
            f"/schedules/{schedule.id}/",
            {
                "starts_at_local": (timezone.now() - timedelta(days=1)).replace(microsecond=0).isoformat(),
            },
            format="json",
        )
        force_authenticate(request, user=self.user)
        resp = ScheduleViewSet.as_view({"patch": "partial_update"})(request, pk=schedule.id)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("starts_at_local", resp.data)

    def test_updating_schedule_rejects_invalid_timezone_name(self):
        schedule = create_schedule(
            board=self.board,
            kind="RECURRING",
            timezone_name="UTC",
            starts_at_local=timezone.now() + timedelta(days=1),
            template={
                "title": "Timezone change",
                "priority": "HIGH",
                "assignee_id": self.user.id,
            },
            recurrence_rule={
                "freq": "DAILY",
                "interval": 1,
            },
            created_by=self.user.email,
        )

        request = self.factory.patch(
            f"/schedules/{schedule.id}/",
            {"timezone": "Mars/Olympus"},
            format="json",
        )
        force_authenticate(request, user=self.user)
        resp = ScheduleViewSet.as_view({"patch": "partial_update"})(request, pk=schedule.id)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("timezone", resp.data)

    def test_timezone_only_update_preserves_wall_clock_time(self):
        starts_at_local = datetime(2026, 4, 10, 9, 0)
        schedule = create_schedule(
            board=self.board,
            kind="RECURRING",
            timezone_name="UTC",
            starts_at_local=starts_at_local,
            template={
                "title": "Timezone preserve",
                "priority": "HIGH",
                "assignee_id": self.user.id,
            },
            recurrence_rule={
                "freq": "DAILY",
                "interval": 1,
            },
            created_by=self.user.email,
        )

        request = self.factory.patch(
            f"/schedules/{schedule.id}/",
            {"timezone": "America/New_York"},
            format="json",
        )
        force_authenticate(request, user=self.user)
        resp = ScheduleViewSet.as_view({"patch": "partial_update"})(request, pk=schedule.id)
        self.assertEqual(resp.status_code, 200)

        schedule.refresh_from_db()
        self.assertEqual(schedule.timezone, "America/New_York")
        localized_start = schedule.starts_at_local.astimezone(ZoneInfo(schedule.timezone))
        self.assertEqual(localized_start.hour, 9)
        self.assertEqual(localized_start.minute, 0)
        self.assertEqual(schedule.starts_at_utc.isoformat(), "2026-04-10T13:00:00+00:00")
        self.assertEqual(schedule.next_run_at_utc, schedule.starts_at_utc)

    def test_monthly_recurrence_with_multiple_monthdays_uses_current_month_candidate(self):
        starts_at_local = parse_local_datetime("2026-04-01T09:00", "UTC")
        after_utc = datetime(2026, 4, 2, 0, 0, tzinfo=dt_timezone.utc)
        next_run = compute_next_occurrence(
            starts_at_local,
            {
                "freq": "MONTHLY",
                "interval": 1,
                "by_monthday": [1, 15],
            },
            after_utc,
            "UTC",
        )
        self.assertEqual(next_run.isoformat(), "2026-04-15T09:00:00+00:00")

    def test_release_due_schedules_is_idempotent_for_same_occurrence(self):
        create_schedule(
            board=self.board,
            kind="ONE_TIME",
            timezone_name="UTC",
            starts_at_local=timezone.now() - timedelta(minutes=1),
            template={
                "title": "Idempotent release",
                "priority": "HIGH",
                "assignee_id": self.user.id,
            },
            recurrence_rule={},
            created_by=self.user.email,
        )

        first = release_due_schedules()
        second = release_due_schedules()

        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(Task.objects.filter(title="Idempotent release").count(), 1)

    def test_overlap_due_occurrence_is_marked_skipped(self):
        schedule = create_schedule(
            board=self.board,
            kind="RECURRING",
            timezone_name="UTC",
            starts_at_local=timezone.now() - timedelta(days=2),
            template={
                "title": "Overlap task",
                "priority": "HIGH",
                "assignee_id": self.user.id,
            },
            recurrence_rule={
                "freq": "DAILY",
                "interval": 1,
            },
            created_by=self.user.email,
        )

        released = release_due_schedules()
        self.assertEqual(released, 1)
        task = Task.objects.get(schedule=schedule)
        task.status = TaskStatus.EXECUTING
        task.save(update_fields=["status"])

        schedule.refresh_from_db()
        schedule.next_run_at_utc = timezone.now() - timedelta(minutes=1)
        schedule.save(update_fields=["next_run_at_utc"])

        released = release_due_schedules()
        self.assertEqual(released, 0)
        latest_run = schedule.runs.order_by("-scheduled_for_utc", "-id").first()
        self.assertEqual(latest_run.status, ScheduleRunStatus.SKIPPED_OVERLAP)

    def test_resume_does_not_backfill_missed_occurrences(self):
        schedule = create_schedule(
            board=self.board,
            kind="RECURRING",
            timezone_name="UTC",
            starts_at_local=timezone.now() - timedelta(days=3),
            template={
                "title": "Resume task",
                "priority": "HIGH",
                "assignee_id": self.user.id,
            },
            recurrence_rule={
                "freq": "DAILY",
                "interval": 1,
            },
            created_by=self.user.email,
        )
        schedule.status = ScheduleStatus.PAUSED
        schedule.paused_at = timezone.now() - timedelta(days=2)
        schedule.next_run_at_utc = timezone.now() - timedelta(days=2)
        schedule.save(update_fields=["status", "paused_at", "next_run_at_utc"])

        request = self.factory.post(f"/schedules/{schedule.id}/resume", {}, format="json")
        force_authenticate(request, user=self.user)
        resp = ScheduleViewSet.as_view({"post": "resume"})(request, pk=schedule.id)
        self.assertEqual(resp.status_code, 200)

        schedule.refresh_from_db()
        self.assertEqual(schedule.status, ScheduleStatus.ACTIVE)
        self.assertGreater(schedule.next_run_at_utc, timezone.now())
        self.assertEqual(schedule.runs.count(), 0)

from datetime import timedelta

from django.utils import timezone

from .base import APITestCase
from tasks.models import Board, BoardMembership, Task, TaskHistory, UserRole


class TestListQueryContract(APITestCase):

    def setUp(self):
        super().setUp()
        self.board1 = self.make_board(name="Board One")
        self.board2 = self.make_board(name="Board Two")
        self.human = self.make_user(name="Human User", email="human@test.com")
        self.agent = self.make_user(name="Agent User", email="agent@odin.agent")
        self.agent.role = UserRole.AGENT
        self.agent.save(update_fields=["role"])
        self.admin = self.make_user(name="Admin User", email="admin@test.com", is_admin=True)
        self.admin.save()

        BoardMembership.objects.create(board=self.board1, user=self.human)
        BoardMembership.objects.create(board=self.board1, user=self.agent)

        self.label_bug = self.make_label(name="bug", color="#ef4444")
        self.label_feature = self.make_label(name="feature", color="#22c55e")

        self.spec_active = self.make_spec(self.board1, odin_id="sp_active", title="Active Spec", abandoned=False)
        self.spec_abandoned = self.make_spec(self.board1, odin_id="sp_abandoned", title="Abandoned Spec", abandoned=True)

        now = timezone.now()
        self.task_a = self.make_task(
            self.board1,
            title="Alpha search task",
            description="Contains keyword alpha",
            status="TODO",
            priority="HIGH",
            assignee=self.human,
            spec=self.spec_active,
        )
        self.task_b = self.make_task(
            self.board1,
            title="Beta timeline task",
            description="Contains keyword beta",
            status="IN_PROGRESS",
            priority="LOW",
            assignee=self.agent,
            spec=self.spec_active,
        )
        self.task_c = self.make_task(
            self.board2,
            title="Gamma other board",
            description="Other board task",
            status="DONE",
            priority="MEDIUM",
        )

        self.task_a.labels.add(self.label_bug)
        self.task_b.labels.add(self.label_feature)

        Task.objects.filter(pk=self.task_a.pk).update(
            created_at=now - timedelta(days=10),
            last_updated_at=now - timedelta(days=9),
        )
        Task.objects.filter(pk=self.task_b.pk).update(
            created_at=now - timedelta(days=5),
            last_updated_at=now - timedelta(days=1),
        )
        self.task_a.refresh_from_db()
        self.task_b.refresh_from_db()

        TaskHistory.objects.create(
            task=self.task_a,
            field_name="created",
            old_value="",
            new_value="Task created",
            changed_by="human@test.com",
        )

    def test_tasks_support_search_filters_sort_and_date_range(self):
        resp = self.client.get(
            f"/tasks/?board_id={self.board1.id}"
            "&search=alpha"
            "&status=TODO,IN_PROGRESS"
            f"&assignee_id={self.human.id}"
            "&priority=HIGH"
            f"&spec_id={self.spec_active.id}"
            f"&label_ids={self.label_bug.id}"
            "&sort=created_at"
            f"&created_from={(timezone.now() - timedelta(days=20)).date().isoformat()}"
            f"&created_to={(timezone.now() - timedelta(days=1)).date().isoformat()}",
        )
        self.assertEqual(resp.status_code, 200)
        results = self.results(resp)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], self.task_a.id)

    def test_tasks_created_date_filter_uses_created_at(self):
        # task_b.created_at is 5 days ago (set in setUp); created range covers last 7 days
        date_from = (timezone.now() - timedelta(days=7)).date().isoformat()
        date_to = timezone.now().date().isoformat()
        resp = self.client.get(
            f"/tasks/?board_id={self.board1.id}&created_from={date_from}&created_to={date_to}"
        )
        self.assertEqual(resp.status_code, 200)
        ids = {item["id"] for item in self.results(resp)}
        self.assertIn(self.task_b.id, ids)

    def test_tasks_invalid_sort_is_400(self):
        resp = self.client.get("/tasks/?sort=unknown_field")
        self.assertEqual(resp.status_code, 400)

    def test_members_support_role_board_and_sort(self):
        resp = self.client.get(f"/api/members/?board_id={self.board1.id}&role=AGENT&sort=-task_count")
        self.assertEqual(resp.status_code, 200)
        results = self.results(resp)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["email"], self.agent.email)
        self.assertEqual(results[0]["role"], "AGENT")

    def test_specs_support_status_filter_and_task_count_sort(self):
        resp = self.client.get(f"/specs/?board_id={self.board1.id}&status=active&sort=-task_count")
        self.assertEqual(resp.status_code, 200)
        results = self.results(resp)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], self.spec_active.id)
        self.assertEqual(results[0]["task_count"], 2)

    def test_boards_support_pagination(self):
        for idx in range(35):
            Board.objects.create(name=f"Board {idx}")
        resp = self.client.get("/boards/?page=2&page_size=10&sort=name")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(self.results(resp)), 10)

    def test_timeline_endpoint_is_unpaginated_and_includes_history(self):
        resp = self.client.get(f"/api/timeline/?board_id={self.board1.id}&sort=created_at,title")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.data, list)
        self.assertEqual(len(resp.data), 2)
        self.assertIn("history", resp.data[0])

    def test_timeline_date_filter_uses_created_at(self):
        # task_b.created_at is 5 days ago (set in setUp); date range covers last 7 days
        date_from = (timezone.now() - timedelta(days=7)).date().isoformat()
        date_to = timezone.now().date().isoformat()
        resp = self.client.get(
            f"/api/timeline/?board_id={self.board1.id}&date_from={date_from}&date_to={date_to}"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.data, list)
        returned_ids = {item["id"] for item in resp.data}
        self.assertIn(self.task_b.id, returned_ids)

    def test_kanban_endpoint_returns_unpaginated_cards(self):
        resp = self.client.get(f"/api/kanban/?board_id={self.board1.id}")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.data, list)
        self.assertEqual(len(resp.data), 2)

    def test_kanban_endpoint_supports_date_range(self):
        date_from = (timezone.now() - timedelta(days=7)).date().isoformat()
        date_to = timezone.now().date().isoformat()
        resp = self.client.get(
            f"/api/kanban/?board_id={self.board1.id}&date_from={date_from}&date_to={date_to}"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.data, list)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]["id"], self.task_b.id)


class TestRoleBackfillDefaults(APITestCase):

    def test_user_role_defaults_and_admin_override(self):
        human = self.make_user(name="Regular", email="regular@test.com")
        self.assertEqual(human.role, "HUMAN")

        agent = self.make_user(name="Agent", email="x@odin.agent")
        agent.role = ""
        agent.save()
        agent.refresh_from_db()
        self.assertEqual(agent.role, "AGENT")

        admin = self.make_user(name="Admin", email="admin2@test.com", is_admin=True)
        admin.save()
        admin.refresh_from_db()
        self.assertEqual(admin.role, "ADMIN")

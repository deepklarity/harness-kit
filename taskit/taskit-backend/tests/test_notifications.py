"""Regression tests for notification endpoints.

Covers the dev-mode user-resolution contract for
``/api/notifications/unread_count/`` and ``/api/notifications/preferences/``.

Bug (2026-07-05): both endpoints returned ``400 Could not resolve user.``
when the dev DB contained only AGENT users (typical after odin runs),
because the resolver filtered the dev fallback by ``role__in=[HUMAN, ADMIN]``.
The list endpoint ``GET /api/notifications/`` returned 200 (empty) for the
same DB, so the resolver was inconsistent with the rest of the board.
"""
from django.test import RequestFactory
from rest_framework.test import force_authenticate

from tasks.models import Notification, NotificationPreference, User, UserRole
from tasks.notification_views import (
    NotificationViewSet,
    notification_preferences,
)

from .base import APITestCase


class NotificationEndpointsDevModeTests(APITestCase):
    """Dev mode (FIREBASE_AUTH_ENABLED=False): resolver falls back gracefully."""

    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()

    def test_unread_count_returns_200_when_only_agent_users_exist(self):
        """Regression: dev DB with only AGENT users must still return 200.

        Pre-fix this returned 400 because the resolver filtered the
        fallback by ``role__in=[HUMAN, ADMIN]`` and found no match.
        """
        User.objects.create(name="Agent Zero", email="a0@odin.agent", role=UserRole.AGENT)
        User.objects.create(name="Agent One", email="a1@odin.agent", role=UserRole.AGENT)

        resp = self.client.get("/api/notifications/unread_count/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("count", resp.data)
        self.assertEqual(resp.data["count"], 0)

    def test_preferences_returns_200_when_only_agent_users_exist(self):
        """Same regression as unread_count, against the preferences endpoint."""
        User.objects.create(name="Agent Zero", email="a0@odin.agent", role=UserRole.AGENT)

        resp = self.client.get("/api/notifications/preferences/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("sound_enabled", resp.data)
        self.assertTrue(resp.data["sound_enabled"])

    def test_list_endpoint_returns_200_in_dev_mode_with_only_agents(self):
        """The list endpoint already worked pre-fix; guard against regression."""
        User.objects.create(name="Agent Zero", email="a0@odin.agent", role=UserRole.AGENT)

        resp = self.client.get("/api/notifications/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 0)

    def test_dev_fallback_prefers_admin_user(self):
        """When both admin and agent users exist, admin wins the fallback."""
        User.objects.create(name="Agent Zero", email="a0@odin.agent", role=UserRole.AGENT)
        admin = User.objects.create(
            name="Admin",
            email="admin@test.com",
            role=UserRole.ADMIN,
            is_admin=True,
        )

        resp = self.client.get("/api/notifications/preferences/")
        self.assertEqual(resp.status_code, 200)

        pref = NotificationPreference.objects.get(user=admin)
        self.assertEqual(pref.user_id, admin.id)

    def test_dev_fallback_uses_first_user_when_no_admin(self):
        """When no admin exists, fallback uses the first user regardless of role."""
        first = User.objects.create(name="First Agent", email="first@odin.agent", role=UserRole.AGENT)

        resp = self.client.get("/api/notifications/preferences/")
        self.assertEqual(resp.status_code, 200)

        pref = NotificationPreference.objects.get(user=first)
        self.assertEqual(pref.user_id, first.id)


class NotificationEndpointsAuthModeTests(APITestCase):
    """Authenticated mode: ``taskit_user`` set on request by middleware."""

    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()
        self.alice = User.objects.create(name="Alice", email="alice@test.com", role=UserRole.HUMAN)
        self.bob = User.objects.create(name="Bob", email="bob@test.com", role=UserRole.HUMAN)

    def test_unread_count_returns_200_for_authenticated_human_user(self):
        request = self.factory.get("/api/notifications/unread_count/")
        force_authenticate(request, user=self.alice)

        view = NotificationViewSet.as_view({"get": "unread_count"})
        resp = view(request)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 0)

    def test_preferences_returns_200_for_authenticated_human_user(self):
        request = self.factory.get("/api/notifications/preferences/")
        force_authenticate(request, user=self.alice)

        resp = notification_preferences(request)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("sound_enabled", resp.data)

    def test_preferences_uses_authenticated_user_for_preference_record(self):
        """The notification preference must be owned by the authenticated user,
        not the dev fallback."""
        request = self.factory.get("/api/notifications/preferences/")
        force_authenticate(request, user=self.alice)

        resp = notification_preferences(request)
        self.assertEqual(resp.status_code, 200)

        pref = NotificationPreference.objects.get(user=self.alice)
        self.assertEqual(pref.user_id, self.alice.id)
        self.assertFalse(NotificationPreference.objects.filter(user=self.bob).exists())

    def test_unread_count_returns_count_for_authenticated_user(self):
        """If the authenticated user has unread notifications, count reflects them."""
        Notification.objects.create(
            recipient=self.alice,
            notification_type="task_assigned",
            title="hello",
            body="world",
        )
        Notification.objects.create(
            recipient=self.bob,
            notification_type="task_assigned",
            title="for bob",
            body="irrelevant",
        )

        request = self.factory.get("/api/notifications/unread_count/")
        force_authenticate(request, user=self.alice)

        view = NotificationViewSet.as_view({"get": "unread_count"})
        resp = view(request)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 1)


class NotificationResolverQueryParamTests(APITestCase):
    """Resolver query-param fallbacks (``?user_id=``, ``?email=``)."""

    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()
        self.alice = User.objects.create(name="Alice", email="alice@test.com", role=UserRole.HUMAN)

    def test_user_id_query_param_resolves_correct_user(self):
        request = self.factory.get(f"/api/notifications/unread_count/?user_id={self.alice.id}")
        view = NotificationViewSet.as_view({"get": "unread_count"})
        resp = view(request)
        self.assertEqual(resp.status_code, 200)

    def test_email_query_param_resolves_correct_user(self):
        request = self.factory.get(f"/api/notifications/preferences/?email={self.alice.email}")
        resp = notification_preferences(request)
        self.assertEqual(resp.status_code, 200)

    def test_invalid_user_id_returns_400(self):
        request = self.factory.get("/api/notifications/unread_count/?user_id=999999")
        view = NotificationViewSet.as_view({"get": "unread_count"})
        resp = view(request)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["detail"], "Could not resolve user.")
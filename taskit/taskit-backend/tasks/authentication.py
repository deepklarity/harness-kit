"""JWT authentication helpers for Taskit."""

from __future__ import annotations

from typing import Tuple

from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.tokens import AccessToken, TokenError

from .models import User


class TaskitAuthError(Exception):
    """Raised when authentication credentials are invalid."""


def _resolve_task_user(auth_user) -> User:
    task_user = (
        User.objects.select_related("auth_user")
        .filter(auth_user_id=auth_user.id)
        .first()
    )
    if not task_user and auth_user.email:
        task_user = User.objects.filter(email=auth_user.email).first()
        if task_user and task_user.auth_user_id is None:
            task_user.auth_user = auth_user
            task_user.save(update_fields=["auth_user"])
    if not task_user:
        raise TaskitAuthError("No matching user account. Contact an admin.")
    return task_user


def resolve_task_user_from_access_token(raw_token: str) -> Tuple[User, object]:
    """Validate an access token and return linked (task_user, django_auth_user)."""
    try:
        token = AccessToken(raw_token)
    except TokenError as exc:
        raise TaskitAuthError("Invalid or expired token.") from exc
    except Exception as exc:
        raise TaskitAuthError("Invalid or expired token.") from exc

    user_id = token.get("user_id")
    if not user_id:
        raise TaskitAuthError("Invalid token payload.")

    auth_user_model = get_user_model()
    try:
        auth_user = auth_user_model.objects.get(id=user_id)
    except auth_user_model.DoesNotExist as exc:
        raise TaskitAuthError("Token user no longer exists.") from exc

    return _resolve_task_user(auth_user), auth_user


class TaskitJWTAuthentication(BaseAuthentication):
    """DRF authentication using access Bearer JWT issued by /auth/login/."""

    def authenticate(self, request):
        if not getattr(settings, "AUTH_ENABLED", False):
            return None

        existing = getattr(request._request, "taskit_user", None)
        existing_auth = getattr(request._request, "auth_user", None)
        if existing is not None and existing_auth is not None:
            return (existing, None)

        header = request.META.get("HTTP_AUTHORIZATION", "")
        if not header.startswith("Bearer "):
            return None

        raw_token = header[7:]
        try:
            task_user, auth_user = resolve_task_user_from_access_token(raw_token)
        except TaskitAuthError as exc:
            raise AuthenticationFailed(str(exc))

        request._request.taskit_user = task_user
        request._request.auth_user = auth_user
        request._request.user = task_user
        return (task_user, None)

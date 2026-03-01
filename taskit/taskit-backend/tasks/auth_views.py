"""Authentication-related views."""

from __future__ import annotations

import json
import logging

from django.conf import settings
from django.contrib.auth import authenticate
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from .authentication import resolve_task_user_from_access_token
from .models import User

logger = logging.getLogger(__name__)


def _set_refresh_cookie(resp: JsonResponse, refresh: str) -> None:
    resp.set_cookie(
        key=settings.AUTH_COOKIE_NAME,
        value=refresh,
        max_age=settings.JWT_REFRESH_SECONDS,
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite=settings.AUTH_COOKIE_SAMESITE,
        domain=settings.AUTH_COOKIE_DOMAIN,
        path=settings.AUTH_COOKIE_PATH,
    )


def _clear_refresh_cookie(resp: JsonResponse) -> None:
    resp.delete_cookie(
        key=settings.AUTH_COOKIE_NAME,
        domain=settings.AUTH_COOKIE_DOMAIN,
        path=settings.AUTH_COOKIE_PATH,
        samesite=settings.AUTH_COOKIE_SAMESITE,
    )


def _task_user_payload(user: User) -> dict:
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "is_admin": user.is_admin,
        "color": user.color,
        "must_change_password": user.must_change_password,
    }


@require_GET
def me(request):
    """Return the current authenticated user's info."""
    if not settings.AUTH_ENABLED:
        return JsonResponse({"detail": "Auth is disabled."}, status=200)

    # Middleware already resolves taskit user on authenticated requests.
    user = getattr(request, "taskit_user", None) or getattr(request, "user", None)
    if user is None:
        return JsonResponse({"detail": "Not authenticated."}, status=401)

    return JsonResponse(_task_user_payload(user))


@csrf_exempt
@require_POST
def login(request):
    """Authenticate with email/password and return an access token.

    POST /auth/login/
    Body: {"email": "...", "password": "..."}

    Returns: {"token": "...", "expires_in": 900, "refresh_expires_in": 604800}
    """
    if not settings.AUTH_ENABLED:
        return JsonResponse({"detail": "Auth is disabled."}, status=200)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"detail": "Invalid JSON body."}, status=400)

    email = body.get("email", "").strip().lower()
    password = body.get("password", "")

    if not email or not password:
        return JsonResponse(
            {"detail": "Both 'email' and 'password' are required."},
            status=400,
        )

    auth_user = authenticate(request, username=email, password=password)
    if not auth_user:
        return JsonResponse({"detail": "Invalid credentials."}, status=401)

    task_user = (
        User.objects.select_related("auth_user")
        .filter(auth_user_id=auth_user.id)
        .first()
    )
    if not task_user:
        # First-login fallback for pre-linked users.
        task_user = User.objects.filter(email=email).first()
        if task_user and task_user.auth_user_id is None:
            task_user.auth_user = auth_user
            task_user.save(update_fields=["auth_user"])

    if not task_user:
        return JsonResponse(
            {"detail": "No matching user account. Contact an admin."},
            status=403,
        )

    refresh = RefreshToken.for_user(auth_user)
    access = str(refresh.access_token)

    payload = {
        "token": access,
        "expires_in": settings.JWT_ACCESS_SECONDS,
        "refresh_expires_in": settings.JWT_REFRESH_SECONDS,
        "user": _task_user_payload(task_user),
    }
    resp = JsonResponse(payload)
    _set_refresh_cookie(resp, str(refresh))
    return resp


@csrf_exempt
@require_POST
def refresh(request):
    """Rotate refresh token from cookie and return a new access token."""
    if not settings.AUTH_ENABLED:
        return JsonResponse({"detail": "Auth is disabled."}, status=200)

    refresh_token = request.COOKIES.get(settings.AUTH_COOKIE_NAME)
    if not refresh_token:
        return JsonResponse({"detail": "Invalid or expired refresh token."}, status=401)

    serializer = TokenRefreshSerializer(data={"refresh": refresh_token})
    try:
        serializer.is_valid(raise_exception=True)
    except Exception:
        return JsonResponse({"detail": "Invalid or expired refresh token."}, status=401)

    data = serializer.validated_data
    token = data["access"]
    resp = JsonResponse({"token": token, "expires_in": settings.JWT_ACCESS_SECONDS})

    new_refresh = data.get("refresh")
    if new_refresh:
        _set_refresh_cookie(resp, new_refresh)

    return resp


@csrf_exempt
@require_POST
def logout(request):
    """Blacklist refresh token from cookie and clear it."""
    if not settings.AUTH_ENABLED:
        return JsonResponse({"detail": "Auth is disabled."}, status=200)

    refresh_token = request.COOKIES.get(settings.AUTH_COOKIE_NAME)
    if refresh_token:
        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
        except TokenError:
            logger.info("Refresh token already invalid during logout")
        except Exception:
            logger.warning("Failed to blacklist refresh token", exc_info=True)

    resp = JsonResponse({"detail": "Logged out."})
    _clear_refresh_cookie(resp)
    return resp


@csrf_exempt
@require_POST
def change_password(request):
    """Change password for the authenticated user."""
    if not settings.AUTH_ENABLED:
        return JsonResponse({"detail": "Auth is disabled."}, status=200)

    auth_header = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth_header.startswith("Bearer "):
        return JsonResponse({"detail": "Authentication required."}, status=401)

    raw_token = auth_header[7:]
    try:
        task_user, auth_user = resolve_task_user_from_access_token(raw_token)
    except Exception:
        return JsonResponse({"detail": "Invalid or expired token."}, status=401)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"detail": "Invalid JSON body."}, status=400)

    old_password = body.get("old_password", "")
    new_password = body.get("new_password", "")
    if not old_password or not new_password:
        return JsonResponse(
            {"detail": "Both 'old_password' and 'new_password' are required."},
            status=400,
        )
    if len(new_password) < 6:
        return JsonResponse(
            {"detail": "New password must be at least 6 characters."},
            status=400,
        )

    if not auth_user.check_password(old_password):
        return JsonResponse({"detail": "Current password is incorrect."}, status=400)

    auth_user.set_password(new_password)
    auth_user.save(update_fields=["password"])
    task_user.must_change_password = False
    task_user.password_changed_at = timezone.now()
    task_user.save(update_fields=["must_change_password", "password_changed_at"])

    return JsonResponse({"detail": "Password updated."}, status=200)

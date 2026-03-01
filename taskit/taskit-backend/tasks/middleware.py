"""Taskit JWT authentication middleware."""
import logging

from django.conf import settings
from django.http import JsonResponse

from .authentication import TaskitAuthError, resolve_task_user_from_access_token

logger = logging.getLogger(__name__)


class TaskitAuthMiddleware:
    """Validates access JWTs from the Authorization header.

    When AUTH_ENABLED is False, this middleware is a no-op
    and all requests pass through unauthenticated.
    """

    # Paths that never require auth
    EXEMPT_PATHS = ["/health/", "/auth/login/", "/auth/refresh/", "/auth/logout/", "/media/"]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not settings.AUTH_ENABLED:
            return self.get_response(request)

        # Skip auth for exempt paths
        if any(request.path.startswith(p) for p in self.EXEMPT_PATHS):
            return self.get_response(request)

        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth_header.startswith("Bearer "):
            return JsonResponse(
                {"detail": "Authentication required."},
                status=401,
            )

        raw_token = auth_header[7:]  # Strip "Bearer "

        try:
            task_user, auth_user = resolve_task_user_from_access_token(raw_token)
        except TaskitAuthError:
            logger.warning("Access token verification failed", exc_info=True)
            return JsonResponse(
                {"detail": "Invalid or expired token."},
                status=401,
            )
        except Exception:
            logger.exception("Unexpected auth middleware failure")
            return JsonResponse(
                {"detail": "Authentication service unavailable."},
                status=503,
            )

        request.user = task_user
        request.taskit_user = task_user
        request.auth_user = auth_user
        return self.get_response(request)

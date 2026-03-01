"""Custom DRF permissions for Taskit auth."""
from django.conf import settings
from rest_framework.permissions import BasePermission


class IsAdmin(BasePermission):
    """Allows access only to admin users.

    When AUTH_ENABLED is False, all requests are permitted.
    """

    def has_permission(self, request, view):
        if not settings.AUTH_ENABLED:
            return True
        return (
            request.user is not None
            and hasattr(request.user, "is_admin")
            and request.user.is_admin
        )

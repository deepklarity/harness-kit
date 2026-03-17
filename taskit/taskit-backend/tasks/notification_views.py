from django.utils.dateparse import parse_datetime
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.response import Response

from .models import Notification, NotificationPreference, User, UserRole
from .serializers import (
    NotificationPreferenceSerializer,
    NotificationSerializer,
)


def _resolve_user(request):
    """Resolve a User instance from the request.

    Resolution order:
    1. request.taskit_user — set by auth middleware when AUTH_ENABLED is True
    2. ?user_id= query param
    3. ?email= query param
    4. First HUMAN user in the database (development fallback)

    Returns a User instance, or None if no user can be resolved.
    """
    user = getattr(request, "taskit_user", None)
    if user is not None:
        return user

    user_id = request.query_params.get("user_id") or request.data.get("user_id")
    if user_id:
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None

    email = request.query_params.get("email") or request.data.get("email")
    if email:
        try:
            return User.objects.get(email=email)
        except User.DoesNotExist:
            return None

    return User.objects.filter(role__in=[UserRole.HUMAN, UserRole.ADMIN]).order_by("id").first()


class NotificationViewSet(viewsets.ModelViewSet):
    queryset = Notification.objects.none()
    serializer_class = NotificationSerializer
    permission_classes = []

    def get_queryset(self):
        user = _resolve_user(self.request)
        if user is None:
            return Notification.objects.none()

        qs = (
            Notification.objects.filter(recipient=user)
            .exclude(body__startswith="{")
            .exclude(body__startswith="[")
            .order_by("-created_at")
        )

        is_read = self.request.query_params.get("is_read")
        if is_read is not None:
            qs = qs.filter(is_read=is_read.lower() in ("true", "1", "yes"))

        notification_type = self.request.query_params.get("type")
        if notification_type:
            qs = qs.filter(notification_type=notification_type)

        since = self.request.query_params.get("since")
        if since:
            parsed = parse_datetime(since)
            if parsed is not None:
                qs = qs.filter(created_at__gte=parsed)

        return qs

    @action(detail=False, methods=["get"])
    def unread_count(self, request):
        user = _resolve_user(request)
        if user is None:
            return Response(
                {"detail": "Could not resolve user."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        count = Notification.objects.filter(recipient=user, is_read=False).count()
        return Response({"count": count})

    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        user = _resolve_user(request)
        if user is None:
            return Response(
                {"detail": "Could not resolve user."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        notification = self.get_object()
        notification.is_read = True
        notification.save(update_fields=["is_read"])
        serializer = self.get_serializer(notification)
        return Response(serializer.data)

    @action(detail=False, methods=["post"])
    def mark_all_read(self, request):
        user = _resolve_user(request)
        if user is None:
            return Response(
                {"detail": "Could not resolve user."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        updated = Notification.objects.filter(recipient=user, is_read=False).update(
            is_read=True
        )
        return Response({"updated": updated})


@api_view(["GET", "PUT"])
def notification_preferences(request):
    user = _resolve_user(request)
    if user is None:
        return Response(
            {"detail": "Could not resolve user."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    preference, _ = NotificationPreference.objects.get_or_create(user=user)

    if request.method == "GET":
        serializer = NotificationPreferenceSerializer(preference)
        return Response(serializer.data)

    serializer = NotificationPreferenceSerializer(
        preference, data=request.data, partial=False
    )
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    serializer.save()
    return Response(serializer.data)

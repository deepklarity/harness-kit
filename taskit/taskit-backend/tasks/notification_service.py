import logging
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)


def _is_quiet_hours(pref) -> bool:
    """Return True if the current moment falls within the user's quiet hours window."""
    if not pref.quiet_hours_start or not pref.quiet_hours_end:
        return False

    try:
        tz = ZoneInfo(pref.quiet_hours_timezone or "UTC")
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")

    now = datetime.now(tz=tz).time()

    try:
        start = datetime.strptime(pref.quiet_hours_start, "%H:%M").time()
        end = datetime.strptime(pref.quiet_hours_end, "%H:%M").time()
    except ValueError:
        logger.warning(
            "Invalid quiet hours format for preference %s: start=%r end=%r",
            pref.pk,
            pref.quiet_hours_start,
            pref.quiet_hours_end,
        )
        return False

    if start <= end:
        return start <= now < end
    # Window wraps midnight (e.g. 22:00 – 07:00)
    return now >= start or now < end


def notify(
    recipient_ids: list,
    notification_type: str,
    title: str,
    body: str = "",
    task=None,
    spec=None,
    board=None,
    actor_email: str = "",
) -> None:
    """Dispatch notifications to a set of recipients.

    Recipients are filtered to HUMAN/ADMIN roles only. The actor (identified
    by actor_email) is excluded to prevent self-notification. Per-user
    NotificationPreference is consulted for disabled types and quiet hours.

    When AUTH_ENABLED is False (dev mode), all HUMAN/ADMIN users are
    automatically included as recipients regardless of board membership.
    """
    from django.conf import settings

    from .models import Notification, NotificationPreference, User, UserRole

    actor_email_lower = actor_email.lower() if actor_email else ""

    # When auth is disabled (dev mode) always include all HUMAN/ADMIN users so
    # that notifications work out of the box without requiring board membership.
    if not getattr(settings, "AUTH_ENABLED", False):
        all_human_ids = list(
            User.objects.filter(role__in=[UserRole.HUMAN, UserRole.ADMIN])
            .values_list("id", flat=True)
        )
        recipient_ids = list(set(recipient_ids) | set(all_human_ids))

    recipients = User.objects.filter(
        id__in=recipient_ids,
        role__in=[UserRole.HUMAN, UserRole.ADMIN],
    ).select_related()

    if actor_email_lower:
        recipients = recipients.exclude(email__iexact=actor_email_lower)

    eligible_recipients = []
    for user in recipients:
        pref, _ = NotificationPreference.objects.get_or_create(user=user)

        disabled = pref.disabled_types or []
        if notification_type in disabled:
            continue

        if _is_quiet_hours(pref):
            continue

        eligible_recipients.append(user)

    if not eligible_recipients:
        return

    notifications = [
        Notification(
            recipient=user,
            notification_type=notification_type,
            title=title,
            body=body,
            task=task,
            spec=spec,
            board=board,
            actor_email=actor_email,
        )
        for user in eligible_recipients
    ]
    Notification.objects.bulk_create(notifications)

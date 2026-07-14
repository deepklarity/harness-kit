from django.urls import path
from rest_framework.routers import DefaultRouter

from . import analytics, views
from . import notification_views

router = DefaultRouter(trailing_slash=True)
router.register(r"users", views.UserViewSet, basename="user")
router.register(r"members", views.UserViewSet, basename="member")
router.register(r"boards", views.BoardViewSet, basename="board")
router.register(r"schedules", views.ScheduleViewSet, basename="schedule")
router.register(r"labels", views.LabelViewSet, basename="label")
router.register(r"tasks", views.TaskViewSet, basename="task")
router.register(r"specs", views.SpecViewSet, basename="spec")
router.register(r"notifications", notification_views.NotificationViewSet, basename="notification")

urlpatterns = [
    path("notifications/preferences/", notification_views.notification_preferences),
    path("presets/", views.list_presets),
    path("system/sandbox-config/", views.sandbox_config),
    path("presets/manage/", views.manage_preset),
    path("user-settings/ide/", views.user_ide_settings),
    path("user-settings/ide/options/", views.user_ide_options),
    path("timeline/", views.timeline),
    path("kanban/", views.kanban),
    path("tasks/search/", views.task_search),
    path("runtime/directories/suggest/", views.runtime_directories_suggest),
    path("runtime/directories/children/", views.runtime_directories_children),
    path("runtime/forced-provider/", views.runtime_forced_provider),
    path("runtime/provider-usage/", views.runtime_provider_usage),
    path("runtime/odin-status/", views.runtime_odin_status),
    path("runtime/process-monitor/", views.runtime_process_monitor),
    path("runtime/stop/", views.runtime_stop),
    path("analytics/cost-summary/", analytics.cost_summary),
    path("analytics/quota-status/", analytics.quota_status),
] + router.urls

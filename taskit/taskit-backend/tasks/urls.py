from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter(trailing_slash=True)
router.register(r"users", views.UserViewSet, basename="user")
router.register(r"boards", views.BoardViewSet, basename="board")
router.register(r"schedules", views.ScheduleViewSet, basename="schedule")
router.register(r"labels", views.LabelViewSet, basename="label")
router.register(r"tasks", views.TaskViewSet, basename="task")
router.register(r"specs", views.SpecViewSet, basename="spec")
router.register(r"reflections", views.ReflectionReportViewSet, basename="reflection")

urlpatterns = [
    path("dashboard/", views.dashboard),
    path("user-settings/ide/", views.user_ide_settings),
    path("user-settings/ide/options/", views.user_ide_options),
    path("errors/<int:event_id>/disposition/", views.error_event_disposition),
    path("executor/capacity/", views.executor_capacity),
    path("executor/max-concurrency/", views.executor_max_concurrency),
] + router.urls

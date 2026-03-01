from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter(trailing_slash=True)
router.register(r"users", views.UserViewSet, basename="user")
router.register(r"members", views.UserViewSet, basename="member")
router.register(r"boards", views.BoardViewSet, basename="board")
router.register(r"labels", views.LabelViewSet, basename="label")
router.register(r"tasks", views.TaskViewSet, basename="task")
router.register(r"specs", views.SpecViewSet, basename="spec")

urlpatterns = [
    path("presets/", views.list_presets),
    path("timeline/", views.timeline),
    path("kanban/", views.kanban),
    path("runtime/directories/suggest/", views.runtime_directories_suggest),
    path("runtime/directories/children/", views.runtime_directories_children),
    path("runtime/odin-status/", views.runtime_odin_status),
    path("runtime/process-monitor/", views.runtime_process_monitor),
    path("runtime/stop/", views.runtime_stop),
] + router.urls

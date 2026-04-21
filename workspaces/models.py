import uuid

from django.contrib.auth.models import User
from django.db import models


DEFAULT_WORKSPACE_ID = "workspace_main"
DEFAULT_WORKSPACE_NAME = "Main"


def generate_workspace_id():
    return f"workspace_{uuid.uuid4().hex[:8]}"


def default_layout_snapshot():
    return {
        "windows": [],
        "minimizedPanels": [],
        "nextZ": 2,
    }


class Workspace(models.Model):
    user = models.ForeignKey(User, related_name="workspaces", on_delete=models.CASCADE)
    workspace_id = models.CharField(max_length=64, default=generate_workspace_id)
    name = models.CharField(max_length=255, default=DEFAULT_WORKSPACE_NAME)
    order = models.PositiveIntegerField(default=0)
    is_default = models.BooleanField(default=False)
    layout_snapshot = models.JSONField(default=default_layout_snapshot, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "workspace_id")
        ordering = ["order", "created_at", "id"]

    def __str__(self):
        return f"{self.user.username}:{self.workspace_id}"


class WorkspacePreference(models.Model):
    user = models.OneToOneField(User, related_name="workspace_preference", on_delete=models.CASCADE)
    active_workspace_id = models.CharField(max_length=64, default=DEFAULT_WORKSPACE_ID)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username}:active={self.active_workspace_id}"

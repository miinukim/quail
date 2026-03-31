"""
Panel and PanelConfig models.
Panels represent reusable experiment UI configurations; PanelConfig stores snapshots.
"""
import uuid
from django.db import models
from django.contrib.auth.models import User
from django.conf import settings


def generate_panel_id():
    return f"pan_{uuid.uuid4().hex[:8]}"

def default_schedule_defaults():
    return {
        'priority': 3,
        'schedule_type': 'NOW',
        'scheduled_at': None,
        'interval_min': None,
        'timezone': getattr(settings, 'TIME_ZONE', 'UTC') or 'UTC',
        'recurrence': None,
    }

def generate_config_id():
    return f"pcfg_{uuid.uuid4().hex[:6]}"

class Panel(models.Model):
    # Human-friendly id used by the frontend.
    panel_id = models.CharField(max_length=32, unique=True, default=generate_panel_id)
    name = models.CharField(max_length=255)
    class_name = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    script_path = models.CharField(max_length=512)
    tags = models.JSONField(default=list, blank=True)
    config_id = models.CharField(max_length=32, null=True, blank=True)

    # Schema & values for UI rendering and defaults.
    param_schema = models.JSONField(default=dict, blank=True)
    param_values = models.JSONField(default=dict, blank=True)
    # Default scheduling used when creating runs.
    schedule_defaults = models.JSONField(default=default_schedule_defaults)

    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Panel {self.panel_id}: {self.name}"


class PanelConfig(models.Model):
    # Snapshot container for a panel's configuration at a point in time.
    config_id = models.CharField(max_length=32, unique=True, default=generate_config_id)
    title = models.CharField(max_length=255)
    note = models.TextField(blank=True)
    tags = models.JSONField(default=list, blank=True)
    panel = models.ForeignKey(Panel, null=True, blank=True, on_delete=models.SET_NULL)
    script_path = models.CharField(max_length=512, blank=True)
    class_name = models.CharField(max_length=255, blank=True)
    snapshot = models.JSONField(default=dict, blank=True)
    updated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"PanelConfig {self.config_id}: {self.title}"

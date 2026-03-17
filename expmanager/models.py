"""
ExperimentScript/Run/Dataset/RunLog models.
These represent the core execution and data artifacts.
"""
import uuid
from django.db import models
from django.contrib.auth.models import User


def generate_schedule_id():
    return f"sch_{uuid.uuid4().hex[:10]}"

class ExperimentScript(models.Model):
    """
    Registry of known Scripts. 
    Ideally this just points to a file, but we keep metadata here.
    """
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    script_path = models.CharField(max_length=512, help_text="Absolute path to the .qil or .py file")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    author = models.ForeignKey(User, on_delete=models.CASCADE)

    def __str__(self):
        return self.name

class ExperimentRun(models.Model):
    STATUS_CHOICES = [
        ('QUEUED', 'Queued'),
        ('RUNNING', 'Running'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
        ('CANCELLED', 'Cancelled'),
        ('ABORTED', 'Aborted'),
    ]

    SCHEDULE_CHOICES = [
        ('NOW', 'Immediate'),
        ('TIMED', 'Scheduled Time'),
        ('RECURRING', 'Recurring'),
    ]

    # Link to the Script registry, OR just a raw path if ad-hoc
    script = models.ForeignKey(ExperimentScript, on_delete=models.SET_NULL, null=True, blank=True)
    script_path = models.CharField(max_length=512, help_text="Path used for this specific run", blank=True)
    name = models.CharField(max_length=255, blank=True, default="")
    
    # Execution arguments
    arguments = models.JSONField(default=dict, blank=True, help_text="Runtime arguments for the function")
    
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='QUEUED')
    
    # Scheduling & Priority
    priority = models.IntegerField(default=2)
    schedule_type = models.CharField(max_length=10, choices=SCHEDULE_CHOICES, default='NOW')
    scheduled_at = models.DateTimeField(null=True, blank=True)
    interval_min = models.IntegerField(null=True, blank=True, help_text="Recurring interval in minutes")
    schedule = models.ForeignKey('ExperimentSchedule', related_name='runs', on_delete=models.SET_NULL, null=True, blank=True)
    dispatch_task_id = models.CharField(max_length=255, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        # Auto-fill script path if linked to a Script object
        if self.script and not self.script_path:
            self.script_path = self.script.script_path
        if not self.name:
            if self.script:
                self.name = self.script.name
            elif self.script_path:
                self.name = self.script_path.split("/")[-1]
        super().save(*args, **kwargs)

    def __str__(self):
        name = self.script.name if self.script else "Ad-hoc Run"
        return f"Run {self.id}: {name} ({self.status})"


class ExperimentSchedule(models.Model):
    STATUS_CHOICES = [
        ('ACTIVE', 'Active'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
        ('FAILED', 'Failed'),
    ]

    SCHEDULE_CHOICES = [
        ('TIMED', 'Scheduled Time'),
        ('RECURRING', 'Recurring'),
    ]

    schedule_id = models.CharField(max_length=32, unique=True, default=generate_schedule_id)
    panel = models.ForeignKey('panels.Panel', null=True, blank=True, on_delete=models.SET_NULL)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=255, blank=True, default="")
    script_path = models.CharField(max_length=512, help_text="Path used for this schedule", blank=True)
    arguments = models.JSONField(default=dict, blank=True, help_text="Runtime arguments for the scheduled execution")
    priority = models.IntegerField(default=2)
    schedule_type = models.CharField(max_length=16, choices=SCHEDULE_CHOICES)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    timezone = models.CharField(max_length=64, default='UTC')
    recurrence = models.JSONField(default=dict, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='ACTIVE')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.name and self.script_path:
            self.name = self.script_path.split("/")[-1]
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Schedule {self.schedule_id}: {self.name} ({self.status})"

class Dataset(models.Model):
    """
    Represents a specific data artifact (HDF5 file) produced by a Run.
    A single Run can produce multiple Datasets.
    """
    experiment_run = models.ForeignKey(ExperimentRun, related_name='datasets', on_delete=models.CASCADE)
    name = models.CharField(max_length=255, help_text="Name of the dataset (e.g. 'Calibration Scan')")
    file_path = models.CharField(max_length=512)
    created_at = models.DateTimeField(auto_now_add=True)
    
    # Optional metadata about the shape/params of this dataset
    metadata = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"{self.name} (Run {self.experiment_run_id})"

class RunLog(models.Model):
    experiment_run = models.ForeignKey(ExperimentRun, related_name='logs', on_delete=models.CASCADE)
    timestamp = models.DateTimeField(auto_now_add=True)
    level = models.CharField(max_length=20, default='INFO')
    message = models.TextField()

    def __str__(self):
        return f"[{self.timestamp}] {self.level}: {self.message}"


class Archive(models.Model):
    """
    Snapshot of a run with selected datasets and metadata.
    """
    title = models.CharField(max_length=255)
    experiment_run = models.ForeignKey(ExperimentRun, related_name='archives', on_delete=models.CASCADE)
    datasets = models.JSONField(default=list, blank=True)
    tags = models.JSONField(default=list, blank=True)
    note = models.TextField(blank=True)
    snapshot_mode = models.CharField(max_length=32, blank=True, default="")
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Archive {self.id} (Run {self.experiment_run_id})"

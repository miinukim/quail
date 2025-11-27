from django.db import models
from django.contrib.auth.models import User

class ExperimentScript(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    script_code = models.TextField(help_text="Main sequence code")
    fpga_code = models.TextField(blank=True, help_text="FPGA real-time code snippet")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    author = models.ForeignKey(User, on_delete=models.CASCADE)

    def __str__(self):
        return self.name

class RunRequest(models.Model):
    STATUS_CHOICES = [
        ('QUEUED', 'Queued'),
        ('RUNNING', 'Running'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
        ('CANCELLED', 'Cancelled'),
    ]

    script = models.ForeignKey(ExperimentScript, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='QUEUED')
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    priority = models.IntegerField(default=0)
    data_file = models.CharField(max_length=255, blank=True, help_text="Path to HDF5 data file")

    def __str__(self):
        return f"Run {self.id}: {self.script.name} ({self.status})"

class RunLog(models.Model):
    run_request = models.ForeignKey(RunRequest, related_name='logs', on_delete=models.CASCADE)
    timestamp = models.DateTimeField(auto_now_add=True)
    level = models.CharField(max_length=20, default='INFO')
    message = models.TextField()

    def __str__(self):
        return f"[{self.timestamp}] {self.level}: {self.message}"

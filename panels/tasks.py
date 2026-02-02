"""
Celery helpers for panel-driven runs.
Supports NOW, TIMED, and RECURRING schedules by enqueueing ExperimentRun tasks.
"""
from celery import shared_task
from .models import Panel
from expmanager.models import ExperimentRun
from expmanager.tasks import run_experiment_task
import datetime


@shared_task(bind=True)
def process_panel_run(self, panel_id, param_values=None, priority=3, schedule_type='NOW', scheduled_at=None, interval_min=None):
    # Normalize priority values into the numeric enum used by ExperimentRun.
    if not isinstance(priority, int):
        try:
            priority = int(priority)
        except Exception:
            mapping = {'LOW': 1, 'NORMAL': 2, 'HIGH': 3, 'CRITICAL': 4}
            priority = mapping.get(str(priority).upper(), 3)
    # Load panel to copy script path and default args.
    panel = Panel.objects.get(panel_id=panel_id)

    # Create a run
    run = ExperimentRun.objects.create(
        script_path=panel.script_path,
        script=None,
        arguments=param_values or panel.param_values or {},
        user=panel.author,
        status='QUEUED',
        name=panel.name,
        priority=priority,
        schedule_type=schedule_type,
        scheduled_at=scheduled_at,
        interval_min=interval_min
    )

    # Dispatch underlying task
    if schedule_type == 'NOW':
        run_experiment_task.delay(run.id)
    elif schedule_type == 'TIMED' and scheduled_at:
        # schedule execution at time
        try:
            dt = datetime.datetime.fromisoformat(scheduled_at)
            run_experiment_task.apply_async((run.id,), eta=dt)
        except Exception:
            run_experiment_task.delay(run.id)
    elif schedule_type == 'RECURRING' and interval_min:
        run_experiment_task.delay(run.id)
        # schedule next recurring execution
        process_panel_run.apply_async((panel_id, param_values, priority, schedule_type, None, interval_min), countdown=int(interval_min) * 60)

    return {'rid': run.id}

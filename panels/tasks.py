"""
Celery helpers for panel-driven runs.
Supports NOW, TIMED, and RECURRING schedules by enqueueing ExperimentRun tasks.
"""
from celery import shared_task
from .models import Panel
from expmanager.scheduling import schedule_run


@shared_task(bind=True)
def process_panel_run(self, panel_id, param_values=None, priority=3, schedule_type='NOW', scheduled_at=None, interval_min=None):
    # Normalize priority into an arbitrary integer.
    if not isinstance(priority, int):
        try:
            priority = int(priority)
        except Exception:
            priority = 3
    # Load panel to copy script path and default args.
    panel = Panel.objects.get(panel_id=panel_id)

    recurrence = None
    if str(schedule_type).upper() == 'RECURRING' and interval_min:
        recurrence = {
            'kind': 'interval',
            'interval_min': interval_min,
            'start_immediately': True,
        }
    response, _run = schedule_run(
        user=panel.author,
        script_path=panel.script_path,
        arguments=param_values or panel.param_values or {},
        name=panel.name,
        priority=priority,
        schedule_type=schedule_type,
        scheduled_at=scheduled_at,
        recurrence=recurrence,
        panel=panel,
    )
    return {'rid': response.get('rid'), 'schedule_id': response.get('schedule_id')}

"""
Defines the Celery tasks for the expmanager app.
Includes the `run_experiment_task` which is the entry point for background experiment execution.
"""
from celery import shared_task
import datetime
import logging
from expmanager.models import ExperimentRun, RunLog
from expmanager.scheduling import cancel_schedule, ensure_next_recurring_run
from orchestrator.engine import ExperimentEngine
import traceback
import os
from django.conf import settings
from django.db.models import Q
from django.utils import timezone


def mark_stale_running_runs(timeout_seconds=None):
    timeout = timeout_seconds
    if timeout is None:
        timeout = getattr(settings, "EXPERIMENT_RUN_STALE_TIMEOUT_SECONDS", 300)
    timeout = max(1, int(timeout))
    now = timezone.now()
    cutoff = now - datetime.timedelta(seconds=timeout)
    stale_runs = list(
        ExperimentRun.objects.filter(status='RUNNING').filter(
            Q(last_heartbeat_at__lt=cutoff)
            | Q(last_heartbeat_at__isnull=True, started_at__lt=cutoff)
        )
    )

    marked = 0
    for experiment_run in stale_runs:
        updated = ExperimentRun.objects.filter(
            id=experiment_run.id,
            status='RUNNING',
        ).update(status='FAILED', completed_at=now)
        if not updated:
            continue
        RunLog.objects.create(
            experiment_run=experiment_run,
            level="ERROR",
            message=(
                "Experiment worker heartbeat was lost; marking run as FAILED. "
                f"Last heartbeat: {experiment_run.last_heartbeat_at or experiment_run.started_at}"
            ),
        )
        marked += 1
    return marked


def _count_consecutive_schedule_failures(schedule, current_run):
    runs = schedule.runs.filter(created_at__lte=current_run.created_at).order_by("-created_at", "-id")
    count = 0
    for run in runs:
        if run.status != 'FAILED':
            break
        count += 1
    return count


def maybe_fail_schedule_after_repeated_failures(experiment_run):
    if not experiment_run.schedule_id or experiment_run.status != 'FAILED':
        return False
    threshold = int(getattr(settings, "EXPERIMENT_SCHEDULE_MAX_CONSECUTIVE_FAILURES", 3))
    if threshold <= 0:
        return False

    schedule = experiment_run.schedule
    if schedule is None or schedule.status != 'ACTIVE':
        return False

    failures = _count_consecutive_schedule_failures(schedule, experiment_run)
    if failures < threshold:
        return False

    cancel_schedule(schedule, mark_failed=True)
    RunLog.objects.create(
        experiment_run=experiment_run,
        level="ERROR",
        message=f"Schedule stopped after {failures} consecutive failed runs.",
    )
    return True


@shared_task(name="expmanager.tasks.mark_stale_running_runs_task")
def mark_stale_running_runs_task():
    return mark_stale_running_runs()


@shared_task(bind=True)
def run_experiment_task(self, run_id):
    logger = logging.getLogger("quail.inspect")
    engine_logger = logging.getLogger("quail.engine")
    logger.info("run_experiment_task start rid=%s task_id=%s", run_id, getattr(self.request, "id", None))
    engine_logger.info("task start rid=%s task_id=%s", run_id, getattr(self.request, "id", None))
    logger.info(
        "run_experiment_task env simulate flag: QUAIL_SIMULATE_RUNS=%s file=%s",
        os.environ.get("QUAIL_SIMULATE_RUNS"),
        __file__,
    )
    engine_logger.info(
        "task env QUAIL_SIMULATE_RUNS=%s file=%s",
        os.environ.get("QUAIL_SIMULATE_RUNS"),
        __file__,
    )
    try:
        experiment_run = ExperimentRun.objects.get(id=run_id)
    except ExperimentRun.DoesNotExist:
        logger.info("run_experiment_task missing run rid=%s", run_id)
        return f"ExperimentRun {run_id} not found"

    if experiment_run.status in {'ABORTED', 'CANCELLED'}:
        logger.info("run_experiment_task skipped rid=%s status=%s", run_id, experiment_run.status)
        return experiment_run.status

    experiment_run.status = 'RUNNING'
    experiment_run.started_at = timezone.now()
    experiment_run.last_heartbeat_at = experiment_run.started_at
    experiment_run.save()

    simulate_env = os.environ.get("QUAIL_SIMULATE_RUNS")
    simulate = simulate_env is not None and simulate_env.lower() in ("1", "true", "yes")

    if simulate:
        RunLog.objects.create(
            experiment_run=experiment_run,
            level="INFO",
            message="Simulated run (no hardware execution)."
        )
        engine_logger.info("task simulated completion rid=%s", run_id)
        experiment_run.status = 'COMPLETED'
        experiment_run.completed_at = timezone.now()
        experiment_run.last_heartbeat_at = experiment_run.completed_at
        experiment_run.save()
        logger.info("run_experiment_task simulated complete rid=%s", run_id)
        return experiment_run.status

    engine = None
    
    try:
        engine = ExperimentEngine(experiment_run)
        engine.run()
        experiment_run.status = 'COMPLETED'
    except InterruptedError:
        experiment_run.refresh_from_db(fields=['status'])
        if experiment_run.status != 'ABORTED':
            experiment_run.status = 'CANCELLED'
        if engine is not None:
            engine.log("Experiment cancelled.")
        else:
            RunLog.objects.create(experiment_run=experiment_run, level="WARNING", message="Experiment cancelled.")
        engine_logger.warning("task interrupted rid=%s", run_id)
    except Exception as e:
        experiment_run.status = 'FAILED'
        if engine is not None:
            engine.log(f"Error: {str(e)}", level="ERROR")
            engine.log(traceback.format_exc(), level="ERROR")
        else:
            RunLog.objects.create(experiment_run=experiment_run, level="ERROR", message=f"Error: {str(e)}")
            RunLog.objects.create(experiment_run=experiment_run, level="ERROR", message=traceback.format_exc())
        engine_logger.exception("task failed rid=%s", run_id)
    finally:
        experiment_run.completed_at = timezone.now()
        experiment_run.last_heartbeat_at = experiment_run.completed_at
        experiment_run.save()
        if experiment_run.schedule_id:
            schedule = experiment_run.schedule
            if schedule is not None:
                schedule.last_run_at = experiment_run.completed_at
                schedule.save(update_fields=['last_run_at', 'updated_at'])
                maybe_fail_schedule_after_repeated_failures(experiment_run)
        logger.info("run_experiment_task finished rid=%s status=%s", run_id, experiment_run.status)
        engine_logger.info("task finished rid=%s status=%s", run_id, experiment_run.status)
        
    return experiment_run.status


@shared_task(bind=True)
def launch_scheduled_run_task(self, run_id):
    logger = logging.getLogger("quail.inspect")
    try:
        experiment_run = ExperimentRun.objects.select_related('schedule').get(id=run_id)
    except ExperimentRun.DoesNotExist:
        logger.info("launch_scheduled_run_task missing run rid=%s", run_id)
        return "missing"

    if experiment_run.status != 'QUEUED':
        logger.info("launch_scheduled_run_task skipping rid=%s status=%s", run_id, experiment_run.status)
        return experiment_run.status

    schedule = experiment_run.schedule
    if schedule is not None and schedule.status != 'ACTIVE':
        experiment_run.status = 'CANCELLED'
        experiment_run.completed_at = timezone.now()
        experiment_run.save(update_fields=['status', 'completed_at'])
        return experiment_run.status

    if schedule is not None:
        try:
            next_run = ensure_next_recurring_run(schedule, after_utc=experiment_run.scheduled_at or timezone.now())
            if next_run is not None and next_run.id != experiment_run.id and not next_run.dispatch_task_id:
                result = launch_scheduled_run_task.apply_async((next_run.id,), eta=next_run.scheduled_at)
                next_run.dispatch_task_id = result.id
                next_run.save(update_fields=['dispatch_task_id'])
        except Exception:
            logger.exception("launch_scheduled_run_task failed creating next recurring run schedule_id=%s", schedule.schedule_id)
            cancel_schedule(schedule, mark_failed=True)
            experiment_run.status = 'FAILED'
            experiment_run.completed_at = timezone.now()
            experiment_run.save(update_fields=['status', 'completed_at'])
            return experiment_run.status

    task = run_experiment_task.delay(experiment_run.id)
    experiment_run.dispatch_task_id = task.id
    experiment_run.save(update_fields=['dispatch_task_id'])
    return "dispatched"

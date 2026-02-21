"""
Defines the Celery tasks for the expmanager app.
Includes the `run_experiment_task` which is the entry point for background experiment execution.
"""
from celery import shared_task
import logging
from expmanager.models import ExperimentRun, RunLog
from orchestrator.engine import ExperimentEngine
import traceback
import os
from django.conf import settings
from django.utils import timezone

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

    experiment_run.status = 'RUNNING'
    experiment_run.started_at = timezone.now()
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
        experiment_run.save()
        logger.info("run_experiment_task simulated complete rid=%s", run_id)
        return experiment_run.status

    engine = ExperimentEngine(experiment_run)
    
    try:
        engine.run()
        experiment_run.status = 'COMPLETED'
    except InterruptedError:
        experiment_run.refresh_from_db(fields=['status'])
        if experiment_run.status != 'ABORTED':
            experiment_run.status = 'CANCELLED'
        engine.log("Experiment cancelled.")
        engine_logger.warning("task interrupted rid=%s", run_id)
    except Exception as e:
        experiment_run.status = 'FAILED'
        engine.log(f"Error: {str(e)}")
        engine.log(traceback.format_exc())
        engine_logger.exception("task failed rid=%s", run_id)
    finally:
        experiment_run.completed_at = timezone.now()
        experiment_run.save()
        logger.info("run_experiment_task finished rid=%s status=%s", run_id, experiment_run.status)
        engine_logger.info("task finished rid=%s status=%s", run_id, experiment_run.status)
        
    return experiment_run.status

"""
Defines the Celery tasks for the expmanager app.
Includes the `run_experiment_task` which is the entry point for background experiment execution.
"""
from celery import shared_task
from expmanager.models import ExperimentRun
from orchestrator.engine import ExperimentEngine
import traceback
from django.utils import timezone

@shared_task(bind=True)
def run_experiment_task(self, run_id):
    try:
        experiment_run = ExperimentRun.objects.get(id=run_id)
    except ExperimentRun.DoesNotExist:
        return f"ExperimentRun {run_id} not found"

    experiment_run.status = 'RUNNING'
    experiment_run.started_at = timezone.now()
    experiment_run.save()

    engine = ExperimentEngine(experiment_run)
    
    try:
        engine.run()
        experiment_run.status = 'COMPLETED'
    except InterruptedError:
        experiment_run.status = 'CANCELLED'
        engine.log("Experiment cancelled.")
    except Exception as e:
        experiment_run.status = 'FAILED'
        engine.log(f"Error: {str(e)}")
        engine.log(traceback.format_exc())
    finally:
        experiment_run.completed_at = timezone.now()
        experiment_run.save()
        
    return experiment_run.status

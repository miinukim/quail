from celery import shared_task
from expmanager.models import RunRequest
from .engine import ExperimentEngine
import traceback
from django.utils import timezone

@shared_task(bind=True)
def run_experiment_task(self, run_id):
    try:
        run_request = RunRequest.objects.get(id=run_id)
    except RunRequest.DoesNotExist:
        return f"RunRequest {run_id} not found"

    run_request.status = 'RUNNING'
    run_request.started_at = timezone.now()
    run_request.save()

    engine = ExperimentEngine(run_request)
    
    try:
        engine.run()
        run_request.status = 'COMPLETED'
    except Exception as e:
        run_request.status = 'FAILED'
        engine.log(f"Error: {str(e)}")
        engine.log(traceback.format_exc())
    finally:
        run_request.completed_at = timezone.now()
        run_request.save()
        
    return run_request.status

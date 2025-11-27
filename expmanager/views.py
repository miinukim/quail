from django.shortcuts import render
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import ExperimentScript, ExperimentRun, RunLog
from .serializers import ExperimentScriptSerializer, ExperimentRunSerializer, RunLogSerializer
from .tasks import run_experiment_task

class ExperimentScriptViewSet(viewsets.ModelViewSet):
    queryset = ExperimentScript.objects.all()
    serializer_class = ExperimentScriptSerializer

class ExperimentRunViewSet(viewsets.ModelViewSet):
    queryset = ExperimentRun.objects.all()
    serializer_class = ExperimentRunSerializer

    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        experiment_run = self.get_object()
        if experiment_run.status != 'QUEUED':
             return Response({'status': 'Experiment is not in QUEUED state'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Trigger Celery task with priority
        # Assuming priority is 0-9, where 9 is highest.
        # ExperimentRun.priority should be mapped/clamped if needed.
        task = run_experiment_task.apply_async(
            args=[experiment_run.id],
            priority=experiment_run.priority
        )
        
        return Response({'status': 'Experiment queued', 'task_id': task.id})

    @action(detail=True, methods=['post'])
    def abort(self, request, pk=None):
        experiment_run = self.get_object()
        if experiment_run.status not in ['QUEUED', 'RUNNING']:
             return Response({'status': f'Experiment is in {experiment_run.status} state, cannot abort'}, status=status.HTTP_400_BAD_REQUEST)
        
        experiment_run.status = 'CANCELLED'
        experiment_run.save()
        
        return Response({'status': 'Experiment aborted'})

class RunLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = RunLog.objects.all()
    serializer_class = RunLogSerializer

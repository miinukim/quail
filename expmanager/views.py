from django.shortcuts import render
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import ExperimentScript, RunRequest, RunLog
from .serializers import ExperimentScriptSerializer, RunRequestSerializer, RunLogSerializer
from .tasks import run_experiment_task

class ExperimentScriptViewSet(viewsets.ModelViewSet):
    queryset = ExperimentScript.objects.all()
    serializer_class = ExperimentScriptSerializer

class RunRequestViewSet(viewsets.ModelViewSet):
    queryset = RunRequest.objects.all()
    serializer_class = RunRequestSerializer

    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        run_request = self.get_object()
        if run_request.status != 'QUEUED':
             return Response({'status': 'Experiment is not in QUEUED state'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Trigger Celery task with priority
        # Assuming priority is 0-9, where 9 is highest.
        # RunRequest.priority should be mapped/clamped if needed.
        task = run_experiment_task.apply_async(
            args=[run_request.id],
            priority=run_request.priority
        )
        
        return Response({'status': 'Experiment queued', 'task_id': task.id})

    @action(detail=True, methods=['post'])
    def abort(self, request, pk=None):
        run_request = self.get_object()
        if run_request.status not in ['QUEUED', 'RUNNING']:
             return Response({'status': f'Experiment is in {run_request.status} state, cannot abort'}, status=status.HTTP_400_BAD_REQUEST)
        
        run_request.status = 'CANCELLED'
        run_request.save()
        
        return Response({'status': 'Experiment aborted'})

class RunLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = RunLog.objects.all()
    serializer_class = RunLogSerializer

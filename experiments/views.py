from django.shortcuts import render
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import ExperimentScript, RunRequest, RunLog
from .serializers import ExperimentScriptSerializer, RunRequestSerializer, RunLogSerializer
from orchestrator.tasks import run_experiment_task

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
        
        # Trigger Celery task
        task = run_experiment_task.delay(run_request.id)
        
        return Response({'status': 'Experiment started', 'task_id': task.id})

class RunLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = RunLog.objects.all()
    serializer_class = RunLogSerializer

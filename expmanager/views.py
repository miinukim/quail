from django.shortcuts import render
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import ExperimentScript, ExperimentRun, RunLog, Dataset
from .serializers import ExperimentScriptSerializer, ExperimentRunSerializer, RunLogSerializer, DatasetSerializer
from .tasks import run_experiment_task

class ExperimentScriptViewSet(viewsets.ModelViewSet):
    queryset = ExperimentScript.objects.all()
    serializer_class = ExperimentScriptSerializer

    def create(self, request, *args, **kwargs):
        # Extract arbitrary code content not in model
        data = request.data.copy()
        script_code = data.pop('script_code', None)
        fpga_code = data.pop('fpga_code', None)
        
        # If code provided, save to file
        if script_code:
            import os
            from django.conf import settings
            filename = f"{data.get('name', 'script')}.qil"
            # Sanitize filename?
            file_dir = os.path.join(settings.BASE_DIR, 'user_scripts')
            os.makedirs(file_dir, exist_ok=True)
            path = os.path.join(file_dir, filename)
            with open(path, 'w') as f:
                f.write(script_code)
            
            data['script_path'] = path
            
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

class ExperimentRunViewSet(viewsets.ModelViewSet):
    queryset = ExperimentRun.objects.all()
    serializer_class = ExperimentRunSerializer

    def perform_create(self, serializer):
        instance = serializer.save()
        
        # If created as QUEUED, try to run immediately (or let Scheduler pick it up)
        # For this prototype, we trigger the task immediately if it's "NOW"
        if instance.status == 'QUEUED' and instance.schedule_type == 'NOW':
            print(f"Auto-starting run {instance.id}")
            task = run_experiment_task.delay(instance.id)

    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        """
        Manually trigger a run if it failed or was paused.
        """
        experiment_run = self.get_object()
        # Trigger Celery task
        try:
            task = run_experiment_task.delay(experiment_run.id)
            return Response({'status': 'Experiment queued', 'task_id': task.id})
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'])
    def abort(self, request, pk=None):
        experiment_run = self.get_object()
        if experiment_run.status not in ['QUEUED', 'RUNNING']:
             return Response({'status': f'Experiment is in {experiment_run.status} state, cannot abort'}, status=status.HTTP_400_BAD_REQUEST)
        
        experiment_run.status = 'CANCELLED'
        experiment_run.save()
        # TODO: Signal the backend worker/celery task to kill the process
        
        return Response({'status': 'Experiment aborted'})

    @action(detail=True, methods=['get'])
    def datasets(self, request, pk=None):
        """
        List all dataset artifacts for this run.
        """
        experiment_run = self.get_object()
        datasets = experiment_run.datasets.all()
        serializer = DatasetSerializer(datasets, many=True)
        return Response(serializer.data)

class DatasetViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Dataset.objects.all()
    serializer_class = DatasetSerializer

    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        # Implementation to stream file content
        return Response({"status": "Not implemented yet"})

import os
from django.conf import settings

class RunLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = RunLog.objects.all()
    serializer_class = RunLogSerializer

class FileViewSet(viewsets.ViewSet):
    """
    ViewSet for managing local files in user_scripts and user_fpga directories.
    """
    
    def _get_directory(self, file_type):
        base_dir = settings.BASE_DIR
        if file_type == 'script':
            return os.path.join(base_dir, 'user_scripts')
        elif file_type == 'fpga':
            return os.path.join(base_dir, 'user_fpga')
        return None

    @action(detail=False, methods=['get'])
    def list_files(self, request):
        file_type = request.query_params.get('type', 'script')
        directory = self._get_directory(file_type)
        
        if not directory or not os.path.exists(directory):
            return Response({'error': 'Directory not found'}, status=status.HTTP_404_NOT_FOUND)
            
        files = []
        for f in os.listdir(directory):
            if os.path.isfile(os.path.join(directory, f)):
                files.append(f)
        
        return Response({'files': sorted(files)})

    @action(detail=False, methods=['get'])
    def read_file(self, request):
        file_type = request.query_params.get('type', 'script')
        filename = request.query_params.get('filename')
        
        if not filename:
            return Response({'error': 'Filename required'}, status=status.HTTP_400_BAD_REQUEST)
            
        directory = self._get_directory(file_type)
        filepath = os.path.join(directory, filename)
        
        # Security check to prevent path traversal
        if not os.path.abspath(filepath).startswith(os.path.abspath(directory)):
             return Response({'error': 'Invalid filename'}, status=status.HTTP_400_BAD_REQUEST)
             
        if not os.path.exists(filepath):
            return Response({'error': 'File not found'}, status=status.HTTP_404_NOT_FOUND)
            
        try:
            with open(filepath, 'r') as f:
                content = f.read()
            return Response({'content': content})
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'])
    def save_file(self, request):
        file_type = request.data.get('type', 'script')
        filename = request.data.get('filename')
        content = request.data.get('content')
        
        if not filename or content is None:
            return Response({'error': 'Filename and content required'}, status=status.HTTP_400_BAD_REQUEST)
            
        directory = self._get_directory(file_type)
        filepath = os.path.join(directory, filename)
        
        # Security check
        if not os.path.abspath(filepath).startswith(os.path.abspath(directory)):
             return Response({'error': 'Invalid filename'}, status=status.HTTP_400_BAD_REQUEST)
             
        try:
            with open(filepath, 'w') as f:
                f.write(content)
            return Response({'status': 'File saved'})
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


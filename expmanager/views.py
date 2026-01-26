from django.shortcuts import render
from rest_framework import viewsets, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import ExperimentScript, ExperimentRun, RunLog, Dataset
from .serializers import ExperimentScriptSerializer, ExperimentRunSerializer, ExperimentRunListSerializer, RunLogSerializer, DatasetSerializer
from .tasks import run_experiment_task
import h5py
import numpy as np
import random
import datetime

class ExperimentScriptViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
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
    permission_classes = [IsAuthenticated]
    queryset = ExperimentRun.objects.all()
    serializer_class = ExperimentRunSerializer

    def _normalize_priority(self, value):
        if value is None:
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            if value.isdigit():
                return int(value)
            mapping = {'LOW': 1, 'NORMAL': 2, 'HIGH': 3, 'CRITICAL': 4}
            return mapping.get(value.upper())
        return None

    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()
        status_q = request.query_params.get('status')
        author_id = request.query_params.get('author_id')
        limit = request.query_params.get('limit')
        if status_q:
            statuses = [s.strip().upper() for s in status_q.split(',') if s.strip()]
            if statuses:
                qs = qs.filter(status__in=statuses)
        if author_id:
            try:
                qs = qs.filter(user_id=int(author_id))
            except Exception:
                pass
        qs = qs.order_by('-created_at')
        if limit:
            try:
                qs = qs[: int(limit)]
            except Exception:
                pass
        serializer = ExperimentRunListSerializer(qs, many=True)
        return Response({'items': serializer.data, 'next_cursor': None})

    def create(self, request, *args, **kwargs):
        data = request.data.copy()
        if request.user and request.user.is_authenticated and 'user' not in data:
            data['user'] = request.user.id
        if 'priority' in data:
            normalized = self._normalize_priority(data.get('priority'))
            if normalized is not None:
                data['priority'] = normalized
        panel_id = data.pop('panel_id', None)
        panel = None
        if panel_id:
            try:
                from quail.panels.models import Panel
                panel = Panel.objects.get(panel_id=panel_id)
                # Use panel defaults if not provided
                data.setdefault('script_path', panel.script_path)
                data.setdefault('arguments', panel.param_values or {})
                data.setdefault('name', panel.name)
            except Exception:
                # Fallback: allow ad-hoc run if script_path is provided
                if not data.get('script_path'):
                    return Response({'error': 'Invalid panel_id'}, status=status.HTTP_400_BAD_REQUEST)

        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        instance = serializer.instance
        if panel is not None:
            param_values = request.data.get('param_values')
            if isinstance(param_values, dict):
                panel.param_values = param_values
                panel.save(update_fields=['param_values', 'updated_at'])
        return Response({'status': instance.status, 'rid': instance.id}, status=status.HTTP_200_OK)

    def perform_create(self, serializer):
        instance = serializer.save()
        
        # If created as QUEUED, try to run immediately (or let Scheduler pick it up)
        # For this prototype, we trigger the task immediately if it's "NOW"
        if instance.status == 'QUEUED' and instance.schedule_type == 'NOW':
            print(f"Auto-starting run {instance.id}")
            try:
                run_experiment_task.delay(instance.id)
            except Exception as e:
                # Don't fail the API call if the worker/broker isn't available.
                print(f"Failed to dispatch run {instance.id}: {e}")

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
        
        experiment_run.status = 'ABORTED'
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
        if not datasets:
            now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')
            return Response([{
                'dataset_id': 'live',
                'name': 'live',
                'dtype': 'float',
                'shape': [0, 2],
                'updated_at': now,
            }])
        serializer = DatasetSerializer(datasets, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='logs')
    def run_logs(self, request, pk=None):
        experiment_run = self.get_object()
        logs = experiment_run.logs.order_by('timestamp').all()
        serializer = RunLogSerializer(logs, many=True)
        return Response({'rid': experiment_run.id, 'items': serializer.data})

    @action(detail=True, methods=['get'], url_path=r'datasets/(?P<dataset_name>[^/]+)/meta')
    def dataset_meta(self, request, pk=None, dataset_name=None):
        experiment_run = self.get_object()
        try:
            dataset = experiment_run.datasets.get(name=dataset_name)
        except Dataset.DoesNotExist:
            if dataset_name == 'live':
                now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')
                return Response({
                    'rid': experiment_run.id,
                    'dataset_id': 'live',
                    'name': 'live',
                    'dtype': 'float',
                    'shape': [0, 2],
                    'updated_at': now,
                })
            return Response({'error': 'Dataset not found'}, status=status.HTTP_404_NOT_FOUND)

        meta = dataset.metadata or {}

        info = {
            'rid': experiment_run.id,
            'dataset_id': f"ds_{dataset.id}",
            'name': dataset.name,
            'metadata': meta,
            'updated_at': dataset.created_at.isoformat().replace('+00:00', 'Z')
        }

        # Try to inspect HDF5 file for dtype/shape
        try:
            if dataset.file_path and os.path.exists(dataset.file_path):
                with h5py.File(dataset.file_path, 'r') as f:
                    if 'data' in f:
                        d = f['data']
                        info['dtype'] = str(d.dtype)
                        info['shape'] = list(d.shape)
        except Exception:
            pass

        return Response(info)

    @action(detail=True, methods=['get'], url_path=r'datasets/(?P<dataset_name>[^/]+)/data')
    def dataset_data(self, request, pk=None, dataset_name=None):
        """GET data with optional ?format=json|npy|hdf5 and ?slice=0:100,:"""
        fmt = request.query_params.get('format', 'json')
        slice_expr = request.query_params.get('slice')
        experiment_run = self.get_object()

        try:
            dataset = experiment_run.datasets.get(name=dataset_name)
        except Dataset.DoesNotExist:
            if dataset_name == 'live' and fmt == 'json':
                points = [[i, random.random()] for i in range(20)]
                return Response({'rid': experiment_run.id, 'dataset_id': 'live', 'name': 'live', 'data': points})
            return Response({'error': 'Dataset not found'}, status=status.HTTP_404_NOT_FOUND)

        if not dataset.file_path or not os.path.exists(dataset.file_path):
            return Response({'error': 'Data file not found'}, status=status.HTTP_404_NOT_FOUND)

        try:
            with h5py.File(dataset.file_path, 'r') as f:
                if 'data' not in f:
                    return Response({'error': 'Data not present in file'}, status=status.HTTP_404_NOT_FOUND)
                arr = np.array(f['data'])

                # Basic slice parser: dims separated by comma, each is start:stop or ':'
                if slice_expr:
                    slices = []
                    for part in slice_expr.split(','):
                        part = part.strip()
                        if part == ':' or part == '':
                            slices.append(slice(None))
                        else:
                            if ':' in part:
                                s,e = part.split(':')
                                start = int(s) if s != '' else None
                                stop = int(e) if e != '' else None
                                slices.append(slice(start, stop))
                            else:
                                idx = int(part)
                                slices.append(idx)
                    arr = arr[tuple(slices)]

                if fmt == 'json':
                    return Response({'rid': experiment_run.id, 'dataset_id': f"ds_{dataset.id}", 'name': dataset.name, 'data': arr.tolist()})
                elif fmt == 'npy':
                    # Return numpy binary as base64? For now, return error (not implemented)
                    return Response({'error': 'npy format not implemented'}, status=status.HTTP_501_NOT_IMPLEMENTED)
                elif fmt == 'hdf5':
                    return Response({'error': 'hdf5 format not implemented'}, status=status.HTTP_501_NOT_IMPLEMENTED)

        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['get'], url_path='get_data')
    def get_data(self, request, pk=None):
        """Aggregate dataset rows into a simple points list for realtime views.

        Response format:
          { 'rid': <run id>, 'points': [ { 'index': i, 'params': [...], 'data': [...] }, ... ] }
        """
        experiment_run = self.get_object()
        points = []

        # Iterate datasets for this run and extract 'data' arrays when possible
        try:
            for ds in experiment_run.datasets.all():
                if not ds.file_path:
                    continue
                try:
                    with h5py.File(ds.file_path, 'r') as f:
                        if 'data' not in f:
                            continue
                        arr = f['data'][:]
                        # If 1D, treat each element as a single vector
                        if arr.ndim == 1:
                            for idx, val in enumerate(arr.tolist()):
                                points.append({'index': idx, 'params': ds.metadata.get('params', []), 'data': [val]})
                        else:
                            for idx, row in enumerate(arr.tolist()):
                                # Keep points reasonably bounded
                                points.append({'index': idx, 'params': ds.metadata.get('params', []), 'data': row})
                except Exception:
                    # Skip dataset on read errors
                    continue

            # Limit the returned points to the most recent 1000 to avoid huge payloads
            if len(points) > 1000:
                points = points[-1000:]

            return Response({'rid': experiment_run.id, 'points': points})
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class DatasetViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    queryset = Dataset.objects.all()
    serializer_class = DatasetSerializer

    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        # Implementation to stream file content
        return Response({"status": "Not implemented yet"})

import os
from django.conf import settings
import datetime

class RunLogViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    queryset = RunLog.objects.all()
    serializer_class = RunLogSerializer

class FileViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
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
    def list(self, request):
        """
        GET /api/files/?type=script|fpga&path=optional/subdir
        Returns a structured listing matching the API spec.
        """
        file_type = request.query_params.get('type', 'script')
        rel_path = request.query_params.get('path', '')
        directory = self._get_directory(file_type)

        if not directory:
            return Response({'error': 'Invalid file type'}, status=status.HTTP_400_BAD_REQUEST)

        target_dir = os.path.normpath(os.path.join(directory, rel_path))

        # Prevent path traversal
        if not target_dir.startswith(os.path.abspath(directory)):
            return Response({'error': 'Invalid path'}, status=status.HTTP_400_BAD_REQUEST)

        if not os.path.exists(target_dir):
            return Response({'error': 'Directory not found'}, status=status.HTTP_404_NOT_FOUND)

        items = []
        for name in sorted(os.listdir(target_dir)):
            full = os.path.join(target_dir, name)
            stat = os.stat(full)
            is_dir = os.path.isdir(full)
            item = {
                'name': name + ('/' if is_dir else ''),
                'kind': 'dir' if is_dir else 'file',
                'size': None if is_dir else stat.st_size,
                'modified_at': datetime.datetime.fromtimestamp(stat.st_mtime, datetime.timezone.utc).isoformat().replace('+00:00', 'Z')
            }
            items.append(item)

        return Response({'type': file_type, 'path': rel_path, 'items': items})

    @action(detail=False, methods=['get'], url_path='read')
    def read(self, request):
        """GET /api/files/read/?type=script|fpga&path=rel/path/to/file"""
        file_type = request.query_params.get('type', 'script')
        rel_path = request.query_params.get('path')

        if not rel_path:
            return Response({'error': 'path required'}, status=status.HTTP_400_BAD_REQUEST)

        directory = self._get_directory(file_type)
        if not directory:
            return Response({'error': 'Invalid file type'}, status=status.HTTP_400_BAD_REQUEST)

        filepath = os.path.normpath(os.path.join(directory, rel_path))

        # Security check
        if not filepath.startswith(os.path.abspath(directory)):
            return Response({'error': 'Invalid path'}, status=status.HTTP_400_BAD_REQUEST)

        if not os.path.exists(filepath):
            return Response({'error': 'File not found'}, status=status.HTTP_404_NOT_FOUND)

        try:
            with open(filepath, 'r') as f:
                content = f.read()
            return Response({'path': rel_path, 'content': content})
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

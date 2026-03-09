"""
REST API endpoints for scripts, runs, logs, datasets, and file browsing.
This is the main entry point for the frontend to manage experiment lifecycle.
"""
from django.shortcuts import render
from rest_framework import viewsets, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import ExperimentScript, ExperimentRun, RunLog, Dataset, Archive
from .serializers import ExperimentScriptSerializer, ExperimentRunSerializer, ExperimentRunListSerializer, RunLogSerializer, DatasetSerializer, ArchiveSerializer
from .tasks import run_experiment_task
import h5py
import numpy as np
import random
import datetime
import time


def _normalize_dataset_for_json(arr: np.ndarray):
    data = np.asarray(arr)
    if data.size == 0:
        return []
    if data.ndim == 3 and data.shape[-1] == 1:
        data = data[:, :, 0]
    return data.tolist()


def _open_h5_read(path: str, retries: int = 4, base_delay: float = 0.05):
    """
    Open an HDF5 file for read while tolerating transient lock contention.
    """
    last_error = None
    for i in range(retries):
        # Try lockless read first, then fallback to default locking mode
        for mode in ('nolock', 'default'):
            try:
                if mode == 'nolock':
                    try:
                        return h5py.File(path, 'r', locking=False)
                    except TypeError:
                        return h5py.File(path, 'r')
                return h5py.File(path, 'r')
            except OSError as e:
                msg = str(e).lower()
                if 'locking flag values don' in msg and mode == 'nolock':
                    last_error = e
                    continue
                if getattr(e, 'errno', None) == 11 or 'unable to lock file' in msg:
                    last_error = e
                    break
                raise
        time.sleep(base_delay * (2 ** i))
    if last_error is not None:
        raise last_error
    raise OSError(f"Failed to open HDF5 file for reading: {path}")


class ExperimentScriptViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    queryset = ExperimentScript.objects.all()
    serializer_class = ExperimentScriptSerializer

    def create(self, request, *args, **kwargs):
        # Support inline script content by writing it to user_scripts and storing path.
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
            file_dir = settings.USER_SCRIPTS_DIR
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
        # Lightweight filtering/pagination for the runs list.
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
        # Allow panel_id-based runs and normalize priority/user fields.
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
        # Simple status-based abort; worker cancellation is a TODO.
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
        serializer = DatasetSerializer(datasets, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='logs')
    def run_logs(self, request, pk=None):
        # Return full log history for a run.
        experiment_run = self.get_object()
        logs = experiment_run.logs.order_by('timestamp').all()
        serializer = RunLogSerializer(logs, many=True)
        return Response({'rid': experiment_run.id, 'items': serializer.data})

    @action(detail=True, methods=['get'], url_path=r'datasets/(?P<dataset_name>[^/]+)/meta')
    def dataset_meta(self, request, pk=None, dataset_name=None):
        # Return dataset metadata; attempts to read HDF5 shape/type when available.
        experiment_run = self.get_object()
        try:
            dataset = experiment_run.datasets.get(name=dataset_name)
        except Dataset.DoesNotExist:
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
                with _open_h5_read(dataset.file_path) as f:
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
            return Response({'error': 'Dataset not found'}, status=status.HTTP_404_NOT_FOUND)

        if not dataset.file_path or not os.path.exists(dataset.file_path):
            return Response({'error': 'Data file not found'}, status=status.HTTP_404_NOT_FOUND)

        try:
            with _open_h5_read(dataset.file_path) as f:
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
                    return Response({'rid': experiment_run.id, 'dataset_id': f"ds_{dataset.id}", 'name': dataset.name, 'data': _normalize_dataset_for_json(arr)})
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
                    with _open_h5_read(ds.file_path) as f:
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


class ArchiveViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    queryset = Archive.objects.all().order_by('-created_at')
    serializer_class = ArchiveSerializer

    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()
        tag = request.query_params.get('tag')
        limit = request.query_params.get('limit')
        if tag:
            qs = qs.filter(tags__contains=[tag])
        if limit:
            try:
                qs = qs[: int(limit)]
            except Exception:
                pass
        items = []
        for a in qs:
            items.append({
                'archive_id': a.id,
                'title': a.title,
                'rid': a.experiment_run_id,
                'datasets': a.datasets or [],
                'tags': a.tags or [],
                'note': a.note,
                'snapshot_mode': a.snapshot_mode,
                'author': {'user_id': a.author_id, 'username': a.author.username if a.author else None},
                'created_at': a.created_at.isoformat().replace('+00:00', 'Z')
            })
        return Response({'items': items, 'next_cursor': None})

    def create(self, request, *args, **kwargs):
        data = request.data.copy()
        rid = data.get('rid') or data.get('experiment_run')
        if not rid:
            return Response({'error': 'rid required'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            run = ExperimentRun.objects.get(id=int(rid))
        except Exception:
            return Response({'error': 'run not found'}, status=status.HTTP_404_NOT_FOUND)
        archive = Archive.objects.create(
            title=data.get('title') or f"Run {run.id} Archive",
            experiment_run=run,
            datasets=data.get('datasets') or [],
            tags=data.get('tags') or [],
            note=data.get('note', ''),
            snapshot_mode=data.get('snapshot_mode', ''),
            author=request.user if request.user.is_authenticated else None
        )
        return Response({'archive_id': archive.id, 'created_at': archive.created_at.isoformat().replace('+00:00', 'Z')}, status=status.HTTP_200_OK)

    def retrieve(self, request, pk=None):
        try:
            a = Archive.objects.get(id=pk)
        except Archive.DoesNotExist:
            return Response({'error': 'archive not found'}, status=status.HTTP_404_NOT_FOUND)
        return Response({
            'archive_id': a.id,
            'title': a.title,
            'rid': a.experiment_run_id,
            'datasets': a.datasets or [],
            'tags': a.tags or [],
            'note': a.note,
            'snapshot_mode': a.snapshot_mode,
            'author': {'user_id': a.author_id, 'username': a.author.username if a.author else None},
            'created_at': a.created_at.isoformat().replace('+00:00', 'Z')
        })

    def partial_update(self, request, pk=None):
        try:
            a = Archive.objects.get(id=pk)
        except Archive.DoesNotExist:
            return Response({'error': 'archive not found'}, status=status.HTTP_404_NOT_FOUND)
        title = request.data.get('title')
        if title is not None:
            a.title = title
        a.save(update_fields=['title'])
        return Response({
            'archive_id': a.id,
            'title': a.title,
            'rid': a.experiment_run_id,
            'datasets': a.datasets or [],
            'tags': a.tags or [],
            'note': a.note,
            'snapshot_mode': a.snapshot_mode,
            'author': {'user_id': a.author_id, 'username': a.author.username if a.author else None},
            'created_at': a.created_at.isoformat().replace('+00:00', 'Z')
        })

    @action(detail=True, methods=['get'], url_path=r'datasets/(?P<dataset_name>[^/]+)/data')
    def dataset_data(self, request, pk=None, dataset_name=None):
        try:
            a = Archive.objects.get(id=pk)
        except Archive.DoesNotExist:
            return Response({'error': 'archive not found'}, status=status.HTTP_404_NOT_FOUND)
        # Reuse run dataset data for now (archive is a snapshot reference).
        run = a.experiment_run
        try:
            dataset = run.datasets.get(name=dataset_name)
        except Dataset.DoesNotExist:
            return Response({'error': 'Dataset not found'}, status=status.HTTP_404_NOT_FOUND)
        if not dataset.file_path or not os.path.exists(dataset.file_path):
            return Response({'error': 'Data file not found'}, status=status.HTTP_404_NOT_FOUND)
        try:
            with _open_h5_read(dataset.file_path) as f:
                if 'data' not in f:
                    return Response({'error': 'Data not present in file'}, status=status.HTTP_404_NOT_FOUND)
                arr = np.array(f['data'])
                return Response({'archive_id': a.id, 'dataset_id': f"ds_{dataset.id}", 'name': dataset.name, 'data': _normalize_dataset_for_json(arr)})
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class FileViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    """
    ViewSet for managing local files in user_scripts and user_fpga directories.
    """
    
    def _get_directory(self, file_type):
        if file_type == 'script':
            return settings.USER_SCRIPTS_DIR
        elif file_type == 'fpga':
            return settings.USER_FPGA_DIR
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

"""
REST API endpoints for scripts, runs, logs, datasets, and file browsing.
This is the main entry point for the frontend to manage experiment lifecycle.
"""
import os
import datetime
import time
import random
import csv

from django.http import HttpResponse
from rest_framework import viewsets, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.response import Response
from django.contrib.auth.models import User
from django.utils import timezone
from .models import ExperimentScript, ExperimentRun, RunLog, Dataset, Archive
from .serializers import ExperimentScriptSerializer, ExperimentRunSerializer, ExperimentRunListSerializer, RunLogSerializer, DatasetSerializer, ArchiveSerializer
from .tasks import run_experiment_task
from .scheduling import (
    cancel_schedule,
    normalize_schedule_type,
    normalize_timezone_name,
    schedule_run,
)
from .dataset_payloads import is_transient_h5_open_error, load_h5_point_table, open_h5_read
from .dataset_query import DatasetQueryError, execute_query


def _positive_int(value, default=None):
    try:
        n = int(value)
    except Exception:
        return default
    return n if n > 0 else default


def _load_dataset_table(dataset, slice_expr=None, limit_rows=None, offset_rows=None):
    return load_h5_point_table(
        dataset.file_path,
        dataset.metadata or {},
        slice_expr=slice_expr,
        limit_rows=limit_rows,
        offset_rows=offset_rows,
    )


def _table_window_metadata(table):
    return {
        'truncated': table.get('truncated', False),
        'row_count': table.get('row_count'),
        'sent_rows': table.get('sent_rows'),
        'total_rows': table.get('total_rows'),
        'returned_rows': table.get('returned_rows'),
        'start_row': table.get('start_row'),
        'end_row': table.get('end_row'),
    }


def _dataset_table_to_csv_response(table, filename):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    columns = table.get('columns', []) or []
    data = table.get('data', []) or []

    if columns:
        writer.writerow(columns)

    for row in data:
        if isinstance(row, dict):
            if columns:
                writer.writerow([row.get(col) for col in columns])
            else:
                if not columns:
                    columns = list(row.keys())
                    writer.writerow(columns)
                writer.writerow([row.get(col) for col in columns])
        elif isinstance(row, (list, tuple)):
            writer.writerow(list(row))
        else:
            writer.writerow([row])

    return response




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
            try:
                return int(value.strip())
            except Exception:
                return None
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
        # Allow panel_id-based runs and normalize scheduling in one place.
        data = request.data.copy()
        if request.user and request.user.is_authenticated and 'user' not in data:
            data['user'] = request.user.id
        panel_id = data.pop('panel_id', None)
        panel = None
        if panel_id:
            try:
                from panels.models import Panel
                panel = Panel.objects.get(panel_id=panel_id)
                # Use panel defaults if not provided
                data.setdefault('script_path', panel.script_path)
                data.setdefault('arguments', data.get('param_values') or panel.param_values or {})
                data.setdefault('name', panel.name)
                defaults = panel.schedule_defaults or {}
                data.setdefault('priority', defaults.get('priority', 3))
                data.setdefault('schedule_type', defaults.get('schedule_type', 'NOW'))
                data.setdefault('scheduled_at', defaults.get('scheduled_at'))
                data.setdefault('interval_min', defaults.get('interval_min'))
                data.setdefault('timezone', defaults.get('timezone', 'UTC'))
                data.setdefault('recurrence', defaults.get('recurrence'))
            except Exception:
                # Fallback: allow ad-hoc run if script_path is provided
                if not data.get('script_path'):
                    return Response({'error': 'Invalid panel_id'}, status=status.HTTP_400_BAD_REQUEST)
        user = self._resolve_user(request, data.get('user'), panel)
        if user is None:
            return Response({'error': 'Authentication required to create a run'}, status=status.HTTP_400_BAD_REQUEST)
        priority = self._normalize_priority(data.get('priority')) or 3
        schedule_type = normalize_schedule_type(data.get('schedule_type'))
        script_path = data.get('script_path')
        if not script_path:
            return Response({'error': 'script_path is required'}, status=status.HTTP_400_BAD_REQUEST)
        arguments = data.get('arguments')
        if not isinstance(arguments, dict):
            arguments = data.get('param_values') if isinstance(data.get('param_values'), dict) else {}
        name = data.get('name') or ""
        timezone_name = normalize_timezone_name(data.get('timezone'))

        try:
            raw_recurrence = data.get('recurrence')
            if not raw_recurrence and data.get('interval_min'):
                raw_recurrence = {
                    'kind': 'interval',
                    'interval_min': data.get('interval_min'),
                    'start_immediately': True,
                }
            response, instance = schedule_run(
                user=user,
                script_path=script_path,
                arguments=arguments,
                name=name,
                priority=priority,
                schedule_type=schedule_type,
                timezone_name=timezone_name,
                scheduled_at=data.get('scheduled_at'),
                recurrence=raw_recurrence,
                panel=panel,
            )
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if panel is not None:
            param_values = request.data.get('param_values')
            if isinstance(param_values, dict):
                panel.param_values = param_values
                panel.save(update_fields=['param_values', 'updated_at'])
        return Response(response, status=status.HTTP_200_OK)

    def perform_create(self, serializer):
        instance = serializer.save()
        if instance.status == 'QUEUED' and instance.schedule_type == 'NOW':
            try:
                task = run_experiment_task.delay(instance.id)
                instance.dispatch_task_id = task.id
                instance.save(update_fields=['dispatch_task_id'])
            except Exception:
                pass

    def _resolve_user(self, request, user_id, panel):
        if request.user and request.user.is_authenticated:
            return request.user
        if panel is not None and panel.author is not None:
            return panel.author
        try:
            if user_id is not None:
                return User.objects.get(id=int(user_id))
        except Exception:
            return None
        return None

    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        """
        Manually retry a failed/cancelled run without allowing duplicate execution.
        """
        experiment_run = self.get_object()
        restartable_statuses = {'FAILED', 'CANCELLED', 'ABORTED'}
        if experiment_run.status not in restartable_statuses:
            return Response(
                {'error': f'Run is in {experiment_run.status} state and cannot be started again.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        updated = ExperimentRun.objects.filter(
            id=experiment_run.id,
            status__in=restartable_statuses,
        ).update(
            status='QUEUED',
            started_at=None,
            completed_at=None,
            last_heartbeat_at=None,
            dispatch_task_id='',
        )
        if not updated:
            return Response(
                {'error': 'Run state changed before it could be queued. Please refresh and retry.'},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            task = run_experiment_task.delay(experiment_run.id)
            ExperimentRun.objects.filter(id=experiment_run.id).update(dispatch_task_id=task.id)
            return Response({'status': 'Experiment queued', 'task_id': task.id})
        except OSError as e:
            ExperimentRun.objects.filter(id=experiment_run.id).update(status='FAILED', completed_at=timezone.now())
            if _is_transient_h5_open_error(e):
                return Response(
                    {'error': 'Dataset file is temporarily unavailable. Please retry shortly.'},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as e:
            ExperimentRun.objects.filter(id=experiment_run.id).update(status='FAILED', completed_at=timezone.now())
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'])
    def abort(self, request, pk=None):
        experiment_run = self.get_object()
        if experiment_run.schedule_id:
            cancel_schedule(experiment_run.schedule)
        if experiment_run.status not in ['QUEUED', 'RUNNING']:
            return Response({'status': f'Experiment is in {experiment_run.status} state, cannot abort'}, status=status.HTTP_400_BAD_REQUEST)
        if experiment_run.dispatch_task_id:
            from .scheduling import revoke_task
            revoke_task(experiment_run.dispatch_task_id)
        experiment_run.status = 'ABORTED'
        experiment_run.completed_at = timezone.now()
        experiment_run.save(update_fields=['status', 'completed_at'])
        return Response({'rid': experiment_run.id, 'status': experiment_run.status})

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
        from_seq = request.query_params.get('from_seq')
        if from_seq is not None:
            try:
                logs = logs.filter(id__gt=int(from_seq))
            except (TypeError, ValueError):
                return Response({'error': 'from_seq must be an integer'}, status=status.HTTP_400_BAD_REQUEST)
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
                with open_h5_read(dataset.file_path) as f:
                    if 'data' in f:
                        d = f['data']
                        info['dtype'] = str(d.dtype)
                        info['shape'] = list(d.shape)
        except Exception:
            pass

        return Response(info)

    @action(detail=True, methods=['get'], url_path=r'datasets/(?P<dataset_name>[^/]+)/data')
    def dataset_data(self, request, pk=None, dataset_name=None):
        """GET data with optional ?format=json, ?slice=0:100,:, ?limit_rows=N, and ?offset=N."""
        fmt = request.query_params.get('format', 'json')
        slice_expr = request.query_params.get('slice')
        limit_rows = request.query_params.get('limit_rows')
        offset_rows = request.query_params.get('offset', request.query_params.get('start_row'))
        experiment_run = self.get_object()

        try:
            dataset = experiment_run.datasets.get(name=dataset_name)
        except Dataset.DoesNotExist:
            return Response({'error': 'Dataset not found'}, status=status.HTTP_404_NOT_FOUND)

        if not dataset.file_path or not os.path.exists(dataset.file_path):
            return Response({'error': 'Data file not found'}, status=status.HTTP_404_NOT_FOUND)

        try:
            table, _combined_meta = _load_dataset_table(
                dataset,
                slice_expr=slice_expr,
                limit_rows=limit_rows,
                offset_rows=offset_rows,
            )

            if fmt == 'json':
                return Response({
                    'rid': experiment_run.id,
                    'dataset_id': f"ds_{dataset.id}",
                    'name': dataset.name,
                    'columns': table['columns'],
                    'data': table['data'],
                    **_table_window_metadata(table),
                })
            elif fmt == 'npy':
                return Response({'error': 'npy format not implemented'}, status=status.HTTP_501_NOT_IMPLEMENTED)
            elif fmt == 'hdf5':
                return Response({'error': 'hdf5 format not implemented'}, status=status.HTTP_501_NOT_IMPLEMENTED)

        except OSError as e:
            if is_transient_h5_open_error(e):
                return Response(
                    {'error': 'Dataset file is temporarily unavailable. Please retry shortly.'},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'], url_path=r'datasets/(?P<dataset_name>[^/]+)/query')
    def dataset_query(self, request, pk=None, dataset_name=None):
        experiment_run = self.get_object()
        try:
            dataset = experiment_run.datasets.get(name=dataset_name)
        except Dataset.DoesNotExist:
            return Response({'error': 'Dataset not found'}, status=status.HTTP_404_NOT_FOUND)

        if not dataset.file_path or not os.path.exists(dataset.file_path):
            return Response({'error': 'Data file not found'}, status=status.HTTP_404_NOT_FOUND)

        query_text = str(request.data.get('query') or '').strip()
        if not query_text:
            return Response({'error': 'query required'}, status=status.HTTP_400_BAD_REQUEST)

        slice_expr = request.data.get('slice')
        try:
            table, _combined_meta = _load_dataset_table(dataset, slice_expr=slice_expr)
            result = execute_query(table.get('columns', []), table.get('data', []), query_text)
            return Response({
                'rid': experiment_run.id,
                'dataset_id': f"ds_{dataset.id}",
                'name': dataset.name,
                'columns': result['columns'],
                'data': result['data'],
                'query': result['query'],
            })
        except DatasetQueryError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except OSError as e:
            if is_transient_h5_open_error(e):
                return Response(
                    {'error': 'Dataset file is temporarily unavailable. Please retry shortly.'},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['get'], url_path=r'datasets/(?P<dataset_name>[^/]+)/download')
    def dataset_download(self, request, pk=None, dataset_name=None):
        experiment_run = self.get_object()
        try:
            dataset = experiment_run.datasets.get(name=dataset_name)
        except Dataset.DoesNotExist:
            return Response({'error': 'Dataset not found'}, status=status.HTTP_404_NOT_FOUND)

        if not dataset.file_path or not os.path.exists(dataset.file_path):
            return Response({'error': 'Data file not found'}, status=status.HTTP_404_NOT_FOUND)

        try:
            table, _combined_meta = _load_dataset_table(dataset)
            safe_name = dataset.name.replace('/', '_')
            filename = f"run_{experiment_run.id}_{safe_name}.csv"
            return _dataset_table_to_csv_response(table, filename)
        except OSError as e:
            if is_transient_h5_open_error(e):
                return Response(
                    {'error': 'Dataset file is temporarily unavailable. Please retry shortly.'},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
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
                    with open_h5_read(ds.file_path) as f:
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

from django.conf import settings

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
            table, _combined_meta = _load_dataset_table(
                dataset,
                slice_expr=request.query_params.get('slice'),
                limit_rows=request.query_params.get('limit_rows'),
                offset_rows=request.query_params.get('offset', request.query_params.get('start_row')),
            )
            return Response({
                'archive_id': a.id,
                'dataset_id': f"ds_{dataset.id}",
                'name': dataset.name,
                'columns': table['columns'],
                'data': table['data'],
                **_table_window_metadata(table),
            })
        except OSError as e:
            if is_transient_h5_open_error(e):
                return Response(
                    {'error': 'Dataset file is temporarily unavailable. Please retry shortly.'},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'], url_path=r'datasets/(?P<dataset_name>[^/]+)/query')
    def dataset_query(self, request, pk=None, dataset_name=None):
        try:
            a = Archive.objects.get(id=pk)
        except Archive.DoesNotExist:
            return Response({'error': 'archive not found'}, status=status.HTTP_404_NOT_FOUND)
        run = a.experiment_run
        try:
            dataset = run.datasets.get(name=dataset_name)
        except Dataset.DoesNotExist:
            return Response({'error': 'Dataset not found'}, status=status.HTTP_404_NOT_FOUND)
        if not dataset.file_path or not os.path.exists(dataset.file_path):
            return Response({'error': 'Data file not found'}, status=status.HTTP_404_NOT_FOUND)

        query_text = str(request.data.get('query') or '').strip()
        if not query_text:
            return Response({'error': 'query required'}, status=status.HTTP_400_BAD_REQUEST)

        slice_expr = request.data.get('slice')
        try:
            table, _combined_meta = _load_dataset_table(dataset, slice_expr=slice_expr)
            result = execute_query(table.get('columns', []), table.get('data', []), query_text)
            return Response({
                'archive_id': a.id,
                'rid': run.id,
                'dataset_id': f"ds_{dataset.id}",
                'name': dataset.name,
                'columns': result['columns'],
                'data': result['data'],
                'query': result['query'],
            })
        except DatasetQueryError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except OSError as e:
            if is_transient_h5_open_error(e):
                return Response(
                    {'error': 'Dataset file is temporarily unavailable. Please retry shortly.'},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['get'], url_path=r'datasets/(?P<dataset_name>[^/]+)/download')
    def dataset_download(self, request, pk=None, dataset_name=None):
        try:
            a = Archive.objects.get(id=pk)
        except Archive.DoesNotExist:
            return Response({'error': 'archive not found'}, status=status.HTTP_404_NOT_FOUND)
        run = a.experiment_run
        try:
            dataset = run.datasets.get(name=dataset_name)
        except Dataset.DoesNotExist:
            return Response({'error': 'Dataset not found'}, status=status.HTTP_404_NOT_FOUND)

        if not dataset.file_path or not os.path.exists(dataset.file_path):
            return Response({'error': 'Data file not found'}, status=status.HTTP_404_NOT_FOUND)

        try:
            table, _combined_meta = _load_dataset_table(dataset)
            safe_name = dataset.name.replace('/', '_')
            filename = f"archive_{a.id}_{safe_name}.csv"
            return _dataset_table_to_csv_response(table, filename)
        except OSError as e:
            if is_transient_h5_open_error(e):
                return Response(
                    {'error': 'Dataset file is temporarily unavailable. Please retry shortly.'},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
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

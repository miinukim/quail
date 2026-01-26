from rest_framework import viewsets, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Panel, PanelConfig
from .serializers import PanelSerializer, PanelCreateSerializer
from expmanager.models import ExperimentScript, ExperimentRun
from expmanager.tasks import run_experiment_task


def panel_default_schedule():
    return {
        'priority': 3,
        'schedule_type': 'NOW',
        'scheduled_at': None,
        'interval_min': None
    }

def normalize_priority(value, default=3):
    if value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        if value.isdigit():
            return int(value)
        mapping = {'LOW': 1, 'NORMAL': 2, 'HIGH': 3, 'CRITICAL': 4}
        return mapping.get(value.upper(), default)
    return default

def build_panel_fields(parameters_schema):
    fields = []
    if not isinstance(parameters_schema, dict):
        return fields
    for key, schema in parameters_schema.items():
        if not isinstance(schema, dict):
            continue
        ftype = schema.get('type')
        if ftype in ('int', 'integer'):
            control = 'integer'
        elif ftype in ('float', 'number'):
            control = 'number'
        elif ftype == 'bool':
            control = 'checkbox'
        else:
            control = 'text'
        fields.append({
            'key': key,
            'control': control,
            'default': schema.get('default'),
            'unit': schema.get('unit'),
        })
    return fields

class PanelViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    queryset = Panel.objects.all()
    serializer_class = PanelSerializer
    lookup_field = 'panel_id'

    def create(self, request, *args, **kwargs):
        # Create a persistent panel from a script_path
        serializer = PanelCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        panel = serializer.save(author=request.user if request.user.is_authenticated else None)

        # Try to inspect the script if it exists in ExperimentScript records
        try:
            script = ExperimentScript.objects.filter(script_path=panel.script_path).first()
            if script:
                # Placeholder for script inspection (no-op for now)
                pass
        except Exception:
            pass

        if not panel.parameters_schema:
            panel.parameters_schema = {
                'detuning': {'type': 'float', 'default': 0.0, 'min': -100.0, 'max': 100.0, 'unit': 'MHz'},
                'shots': {'type': 'int', 'default': 100, 'min': 1, 'max': 100000},
                'use_cooling': {'type': 'bool', 'default': True},
                'sweep_points': {'type': 'iterable', 'default': [0.0, 0.5, 1.0]},
            }
            panel.save(update_fields=['parameters_schema', 'updated_at'])

        if not panel.panel_fields and panel.parameters_schema:
            panel.panel_fields = build_panel_fields(panel.parameters_schema)
            panel.save(update_fields=['panel_fields', 'updated_at'])

        out = PanelSerializer(panel).data
        out['config_id'] = panel.config_id
        # Provide panel UI defaults as in spec
        out['parameters_schema'] = panel.parameters_schema or {}
        out['param_values'] = panel.param_values or {}
        out['panel'] = {
            'fields': panel.panel_fields or build_panel_fields(panel.parameters_schema),
            'schedule_defaults': panel.schedule_defaults or panel_default_schedule()
        }

        return Response(out, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='from-config')
    def from_config(self, request):
        config_id = request.data.get('config_id')
        if not config_id:
            return Response({'error': 'config_id required'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            cfg = PanelConfig.objects.get(config_id=config_id)
        except PanelConfig.DoesNotExist:
            return Response({'error': 'config not found'}, status=status.HTTP_404_NOT_FOUND)

        snapshot = cfg.snapshot or {}
        panel = Panel.objects.create(
            name=snapshot.get('name') or cfg.title,
            class_name=snapshot.get('class_name', ''),
            description=snapshot.get('description', ''),
            script_path=snapshot.get('script_path', cfg.script_path),
            tags=cfg.tags or [],
            config_id=cfg.config_id,
            param_values=snapshot.get('param_values', {}),
            parameters_schema=snapshot.get('parameters_schema', {}),
            panel_fields=snapshot.get('panel_fields', {}),
            schedule_defaults=snapshot.get('queue_defaults', panel_default_schedule()),
            author=request.user if request.user.is_authenticated else None,
        )

        out = PanelSerializer(panel).data
        out['config_id'] = cfg.config_id
        out['config_meta'] = {'title': cfg.title, 'updated_at': cfg.updated_at.isoformat().replace('+00:00', 'Z')}
        out['parameters_schema'] = panel.parameters_schema or {}
        out['param_values'] = panel.param_values or {}
        out['panel'] = {
            'fields': panel.panel_fields or build_panel_fields(panel.parameters_schema),
            'schedule_defaults': panel.schedule_defaults or panel_default_schedule()
        }
        return Response(out, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='inspect-classes')
    def inspect_classes(self, request):
        script_path = request.data.get('script_path')
        if not script_path:
            return Response({'error': 'script_path required'}, status=status.HTTP_400_BAD_REQUEST)
        # Placeholder: replace with real script inspection
        classes = ['CoolingScan', 'RabiScan', 'RamseyScan']
        return Response({'script_path': script_path, 'classes': classes}, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'])
    def list_panels(self, request):
        # legacy action - provide same output as list()
        return self.list(request)

    def list(self, request):
        qs = self.get_queryset()
        script_path = request.query_params.get('script_path')
        tag = request.query_params.get('tag')
        limit = request.query_params.get('limit')
        if script_path:
            qs = qs.filter(script_path=script_path)
        serializer = PanelSerializer(qs, many=True)
        items = serializer.data
        if tag:
            items = [x for x in items if tag in (x.get('tags') or [])]
        if limit:
            try:
                items = items[: int(limit)]
            except Exception:
                pass
        return Response({'items': items, 'next_cursor': None})

    def retrieve(self, request, panel_id=None):
        panel = self.get_object()
        data = PanelSerializer(panel).data
        if panel.config_id:
            cfg = PanelConfig.objects.filter(config_id=panel.config_id).first()
            if cfg:
                data['config_id'] = cfg.config_id
                data['config_meta'] = {'title': cfg.title, 'updated_at': cfg.updated_at.isoformat().replace('+00:00', 'Z')}
        data['parameters_schema'] = panel.parameters_schema or {}
        data['param_values'] = panel.param_values or {}
        data['panel'] = {'fields': panel.panel_fields or build_panel_fields(panel.parameters_schema), 'schedule_defaults': panel.schedule_defaults}
        return Response(data)

    @action(detail=True, methods=['post'])
    def run(self, request, panel_id=None):
        """Expose endpoint to create a run from a panel and optional overrides"""
        panel = self.get_object()
        body = request.data.copy()
        param_values = body.get('param_values', panel.param_values or {})
        priority = normalize_priority(body.get('priority', panel.schedule_defaults.get('priority', 3)))
        schedule_type = body.get('schedule_type', panel.schedule_defaults.get('schedule_type', 'NOW'))
        scheduled_at = body.get('scheduled_at', panel.schedule_defaults.get('scheduled_at'))
        interval_min = body.get('interval_min', panel.schedule_defaults.get('interval_min'))

        # Create ExperimentRun
        user_for_run = request.user if request.user.is_authenticated else panel.author
        if user_for_run is None:
            return Response({'error': 'Authentication required to start run or panel lacks author'}, status=status.HTTP_400_BAD_REQUEST)

        # If NOW, create run immediately and dispatch
        if schedule_type == 'NOW' or schedule_type == 'now':
            run = ExperimentRun.objects.create(
                script_path=panel.script_path,
                script=None,
                arguments=param_values,
                user=user_for_run,
                name=panel.name,
                status='QUEUED',
                priority=priority,
                schedule_type=schedule_type,
                scheduled_at=scheduled_at if scheduled_at else None,
                interval_min=interval_min
            )
            try:
                run_experiment_task.delay(run.id)
            except Exception as e:
                print(f"Failed to dispatch run {run.id}: {e}")
            return Response({'panel_id': panel.panel_id, 'status': 'QUEUED', 'name': panel.name, 'parameters_schema': panel.parameters_schema})
        else:
            # For TIMED or RECURRING, schedule via panels.tasks to manage recurring behavior
            from .tasks import process_panel_run
            process_panel_run.apply_async((panel.panel_id, param_values, priority, schedule_type, scheduled_at, interval_min))
            return Response({'panel_id': panel.panel_id, 'status': 'SCHEDULED', 'name': panel.name})

        return Response({'panel_id': panel.panel_id, 'status': 'QUEUED', 'name': panel.name, 'parameters_schema': panel.parameters_schema})


class PanelConfigViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    lookup_field = 'config_id'

    def list(self, request):
        qs = PanelConfig.objects.all().order_by('-updated_at')
        tag = request.query_params.get('tag')
        script_path = request.query_params.get('script_path')
        class_name = request.query_params.get('class_name')
        limit = request.query_params.get('limit')

        if script_path:
            qs = qs.filter(script_path=script_path)
        if class_name:
            qs = qs.filter(class_name=class_name)

        items = []
        for cfg in qs:
            if tag and tag not in (cfg.tags or []):
                continue
            items.append({
                'config_id': cfg.config_id,
                'title': cfg.title,
                'script_path': cfg.script_path,
                'class_name': cfg.class_name,
                'tags': cfg.tags or [],
                'updated_at': cfg.updated_at.isoformat().replace('+00:00', 'Z'),
                'updated_by': {'user_id': cfg.updated_by_id, 'username': cfg.updated_by.username if cfg.updated_by else None},
            })
            if limit and len(items) >= int(limit):
                break
        return Response({'items': items, 'next_cursor': None})

    def retrieve(self, request, pk=None):
        try:
            cfg = PanelConfig.objects.get(config_id=pk)
        except PanelConfig.DoesNotExist:
            return Response({'error': 'config not found'}, status=status.HTTP_404_NOT_FOUND)
        return Response({
            'config_id': cfg.config_id,
            'title': cfg.title,
            'note': cfg.note,
            'tags': cfg.tags or [],
            'snapshot': cfg.snapshot or {},
            'updated_at': cfg.updated_at.isoformat().replace('+00:00', 'Z'),
            'updated_by': {'user_id': cfg.updated_by_id, 'username': cfg.updated_by.username if cfg.updated_by else None},
        })

    def create(self, request):
        data = request.data.copy()
        panel_id = data.get('panel_id')
        snapshot = data.get('snapshot') or {}
        panel = None
        if panel_id:
            panel = Panel.objects.filter(panel_id=panel_id).first()

        cfg = PanelConfig.objects.create(
            title=data.get('title', ''),
            note=data.get('note', ''),
            tags=data.get('tags') or [],
            panel=panel,
            script_path=snapshot.get('script_path') or (panel.script_path if panel else ''),
            class_name=snapshot.get('class_name') or (panel.class_name if panel else ''),
            snapshot=snapshot,
            updated_by=request.user if request.user.is_authenticated else None,
        )
        return Response({'config_id': cfg.config_id, 'created_at': cfg.created_at.isoformat().replace('+00:00', 'Z')}, status=status.HTTP_200_OK)

    def destroy(self, request, pk=None):
        try:
            cfg = PanelConfig.objects.get(config_id=pk)
        except PanelConfig.DoesNotExist:
            return Response(status=status.HTTP_204_NO_CONTENT)
        cfg.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'], url_path='apply-panel')
    def apply_panel(self, request, pk=None):
        try:
            cfg = PanelConfig.objects.get(config_id=pk)
        except PanelConfig.DoesNotExist:
            return Response({'error': 'config not found'}, status=status.HTTP_404_NOT_FOUND)

        panel_id = request.data.get('panel_id')
        mode = request.data.get('mode', 'overwrite')
        fields = request.data.get('fields') or []

        panel = Panel.objects.filter(panel_id=panel_id).first()
        if not panel:
            return Response({'error': 'panel not found'}, status=status.HTTP_404_NOT_FOUND)

        if mode == 'overwrite':
            snapshot = cfg.snapshot or {}
            if not fields or 'param_values' in fields:
                snapshot['param_values'] = panel.param_values or {}
            if not fields or 'queue_defaults' in fields:
                snapshot['queue_defaults'] = panel.schedule_defaults or panel_default_schedule()
            snapshot.setdefault('script_path', panel.script_path)
            snapshot.setdefault('class_name', panel.class_name)
            cfg.snapshot = snapshot
            cfg.script_path = panel.script_path
            cfg.class_name = panel.class_name

        cfg.updated_by = request.user if request.user.is_authenticated else None
        cfg.save(update_fields=['snapshot', 'script_path', 'class_name', 'updated_by', 'updated_at'])

        return Response({
            'config_id': cfg.config_id,
            'updated_at': cfg.updated_at.isoformat().replace('+00:00', 'Z'),
            'updated_by': {'user_id': cfg.updated_by_id, 'username': cfg.updated_by.username if cfg.updated_by else None},
        })

    @action(detail=False, methods=['post'], url_path='from-panel')
    def from_panel(self, request):
        panel_id = request.data.get('panel_id')
        title = request.data.get('title')
        tags = request.data.get('tags') or []
        if not panel_id or not title:
            return Response({'error': 'panel_id and title required'}, status=status.HTTP_400_BAD_REQUEST)

        panel = Panel.objects.filter(panel_id=panel_id).first()
        if not panel:
            return Response({'error': 'panel not found'}, status=status.HTTP_404_NOT_FOUND)

        snapshot = {
            'script_path': panel.script_path,
            'class_name': panel.class_name,
            'param_values': panel.param_values or {},
            'parameters_schema': panel.parameters_schema or {},
            'panel_fields': panel.panel_fields or build_panel_fields(panel.parameters_schema),
            'queue_defaults': panel.schedule_defaults or panel_default_schedule(),
        }
        cfg = PanelConfig.objects.create(
            title=title,
            tags=tags,
            panel=panel,
            script_path=panel.script_path,
            class_name=panel.class_name,
            snapshot=snapshot,
            updated_by=request.user if request.user.is_authenticated else None,
        )
        return Response({'config_id': cfg.config_id, 'created_at': cfg.created_at.isoformat().replace('+00:00', 'Z')}, status=status.HTTP_200_OK)

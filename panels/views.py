"""
Panels API: persistent panel definitions, config snapshots, and run scheduling.
Panels allow users to reuse scripts with saved parameters and scheduling defaults.
"""
from rest_framework import viewsets, status
import logging
import os
from django.conf import settings
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Panel, PanelConfig
from .serializers import PanelSerializer, PanelCreateSerializer
from expmanager.models import ExperimentScript
from expmanager.scheduling import normalize_timezone_name, schedule_run
from orchestrator.inspector import inspect_script


DEFAULT_PANEL_SCHEMA = {
    'detuning': {'type': 'float', 'default': 0.0, 'min': -100.0, 'max': 100.0, 'unit': 'MHz'},
    'shots': {'type': 'int', 'default': 100, 'min': 1, 'max': 100000},
    'use_cooling': {'type': 'bool', 'default': True},
    'sweep_points': {'type': 'iterable', 'default': [0.0, 0.5, 1.0]},
}

def panel_default_schedule():
    return {
        'priority': 3,
        'schedule_type': 'NOW',
        'scheduled_at': None,
        'interval_min': None,
        'timezone': getattr(settings, 'TIME_ZONE', 'UTC') or 'UTC',
        'recurrence': None,
    }

def normalize_priority(value, default=3):
    if value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except Exception:
            return default
    return default

logger = logging.getLogger("quail.inspect")

def resolve_script_path(path):
    if not path:
        return None
    import os
    if os.path.isabs(path) and os.path.exists(path):
        return path
    # Try relative to configured script dirs
    for base in (settings.USER_SCRIPTS_DIR, settings.BASE_DIR):
        candidate = os.path.join(base, path)
        if os.path.exists(candidate):
            return candidate
    return path

def build_panel_fields(parameters_schema):
    # Map schema types to simple UI control hints.
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
        logger.info("Panel create: panel_id=%s script_path=%s", panel.panel_id, panel.script_path)

        # Inspect script on create to infer parameter schema.
        # Prefer the panel's script_path directly (no DB record required).
        inspected = False
        try:
            import os
            script_path = resolve_script_path(panel.script_path)
            if not script_path:
                script = ExperimentScript.objects.filter(script_path=panel.script_path).first()
                script_path = resolve_script_path(script.script_path) if script else None
            if not script_path:
                logger.info("Panel inspection (create) skipped: no script_path panel_id=%s", panel.panel_id)
            if script_path and os.path.exists(script_path):
                with open(script_path, 'r') as f:
                    code = f.read()
                inspection = inspect_script(code)
                if isinstance(inspection, dict):
                    schema = inspection.get('parameters_schema') or {}
                    param_values = inspection.get('param_values') or {}
                    inspected = True
                    logger.info("Panel inspection (create) path=%s schema=%s param_values=%s", script_path, schema, param_values)
                    if schema:
                        panel.parameters_schema = schema
                    if param_values:
                        panel.param_values = param_values
                    if panel.parameters_schema:
                        panel.panel_fields = build_panel_fields(panel.parameters_schema)
                    if schema or param_values:
                        panel.save(update_fields=['parameters_schema', 'param_values', 'panel_fields', 'updated_at'])
        except Exception:
            logger.exception("Panel inspection (create) failed for path=%s", panel.script_path)

        # Fallback schema only when no script path is available.
        if not panel.parameters_schema and not inspected and not panel.script_path:
            panel.parameters_schema = DEFAULT_PANEL_SCHEMA
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
        # Create a new panel instance from a stored config snapshot.
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
        return Response({}, status=status.HTTP_501_NOT_IMPLEMENTED)

    @action(detail=False, methods=['get'])
    def listPanels(self, request):
        # Basic filter support for panels.
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
        # Return panel plus expanded config metadata for the UI.
        panel = self.get_object()
        logger.info("Panel retrieve: panel_id=%s script_path=%s", panel.panel_id, panel.script_path)
        # If panel still has the default template schema, attempt re-inspection.
        try:
            if panel.script_path and (not panel.parameters_schema or panel.parameters_schema == DEFAULT_PANEL_SCHEMA):
                resolved = resolve_script_path(panel.script_path)
                if resolved and os.path.exists(resolved):
                    with open(resolved, 'r') as f:
                        code = f.read()
                    inspection = inspect_script(code)
                    if isinstance(inspection, dict):
                        schema = inspection.get('parameters_schema') or {}
                        param_values = inspection.get('param_values') or {}
                        logger.info("Panel inspection (retrieve) path=%s schema=%s param_values=%s", resolved, schema, param_values)
                        if schema:
                            panel.parameters_schema = schema
                        if param_values:
                            panel.param_values = param_values
                        if panel.parameters_schema:
                            panel.panel_fields = build_panel_fields(panel.parameters_schema)
                        if schema or param_values:
                            panel.save(update_fields=['parameters_schema', 'param_values', 'panel_fields', 'updated_at'])
                else:
                    logger.info("Panel inspection (retrieve) skipped: path not found=%s", panel.script_path)
        except Exception:
            logger.exception("Panel inspection (retrieve) failed for path=%s", panel.script_path)
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
        schedule_type = str(body.get('schedule_type', panel.schedule_defaults.get('schedule_type', 'NOW'))).upper()
        scheduled_at = body.get('scheduled_at', panel.schedule_defaults.get('scheduled_at'))
        interval_min = body.get('interval_min', panel.schedule_defaults.get('interval_min'))
        timezone_name = normalize_timezone_name(body.get('timezone', panel.schedule_defaults.get('timezone', 'UTC')))
        recurrence = body.get('recurrence', panel.schedule_defaults.get('recurrence'))

        if isinstance(param_values, dict):
            panel.param_values = param_values
            panel.save(update_fields=['param_values', 'updated_at'])

        # Create ExperimentRun
        user_for_run = request.user if request.user.is_authenticated else panel.author
        if user_for_run is None:
            return Response({'error': 'Authentication required to start run or panel lacks author'}, status=status.HTTP_400_BAD_REQUEST)
        if schedule_type == 'RECURRING' and not recurrence and interval_min:
            recurrence = {
                'kind': 'interval',
                'interval_min': interval_min,
                'start_immediately': True,
            }
        try:
            response, _instance = schedule_run(
                user=user_for_run,
                script_path=panel.script_path,
                arguments=param_values if isinstance(param_values, dict) else {},
                name=panel.name,
                priority=priority,
                schedule_type=schedule_type,
                timezone_name=timezone_name,
                scheduled_at=scheduled_at,
                recurrence=recurrence,
                panel=panel,
            )
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        response['panel_id'] = panel.panel_id
        response['name'] = panel.name
        response['parameters_schema'] = panel.parameters_schema
        return Response(response)


class PanelConfigViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    lookup_field = 'config_id'

    def partial_update(self, request, pk=None, config_id=None):
        try:
            cfg = PanelConfig.objects.get(config_id=config_id or pk)
        except PanelConfig.DoesNotExist:
            return Response({'error': 'config not found'}, status=status.HTTP_404_NOT_FOUND)
        title = request.data.get('title')
        if title is not None:
            cfg.title = title
        cfg.updated_by = request.user if request.user.is_authenticated else None
        cfg.save(update_fields=['title', 'updated_by', 'updated_at'])
        return Response({
            'config_id': cfg.config_id,
            'updated_at': cfg.updated_at.isoformat().replace('+00:00', 'Z'),
            'updated_by': {'user_id': cfg.updated_by_id, 'username': cfg.updated_by.username if cfg.updated_by else None},
        })

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

    def destroy(self, request, pk=None, config_id=None):
        try:
            cfg = PanelConfig.objects.get(config_id=config_id or pk)
        except PanelConfig.DoesNotExist:
            return Response(status=status.HTTP_204_NO_CONTENT)
        cfg.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'], url_path='apply-panel')
    def apply_panel(self, request, pk=None, config_id=None):
        cfg_id = config_id or pk
        try:
            cfg = PanelConfig.objects.get(config_id=cfg_id)
        except PanelConfig.DoesNotExist:
            return Response({'error': 'config not found'}, status=status.HTTP_404_NOT_FOUND)

        panel_id = request.data.get('panel_id')
        mode = request.data.get('mode', 'overwrite')
        fields = request.data.get('fields') or []
        req_param_values = request.data.get('param_values')
        req_queue_defaults = request.data.get('queue_defaults') or request.data.get('schedule_defaults')

        panel = Panel.objects.filter(panel_id=panel_id).first()
        if not panel:
            return Response({'error': 'panel not found'}, status=status.HTTP_404_NOT_FOUND)

        if mode == 'overwrite':
            snapshot = cfg.snapshot if isinstance(cfg.snapshot, dict) else {}
            panel_params = panel.param_values if isinstance(panel.param_values, dict) else {}
            panel_defaults = panel.schedule_defaults if isinstance(panel.schedule_defaults, dict) else panel_default_schedule()
            if isinstance(req_param_values, dict):
                panel_params = req_param_values
            if isinstance(req_queue_defaults, dict):
                panel_defaults = req_queue_defaults
            if not fields or 'param_values' in fields:
                snapshot['param_values'] = panel_params
            if not fields or 'queue_defaults' in fields:
                snapshot['queue_defaults'] = panel_defaults
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

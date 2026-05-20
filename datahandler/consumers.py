"""
WebSocket consumers for the datahandler app.
Handles real-time communication for run status, logs, and dataset streams.
"""
import json
import asyncio
import random
import datetime
import h5py
from django.conf import settings
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from core.ttl_state import TTL_DEVICES, TTL_STATE
from expmanager.dataset_query import DatasetQueryError, execute_query
from expmanager.models import Dataset, ExperimentRun, RunLog
from expmanager.dataset_payloads import load_h5_point_table
from expmanager.serializers import ExperimentRunListSerializer


STREAM_SNAPSHOT_MAX_ROWS = int(getattr(settings, "DATASET_STREAM_SNAPSHOT_MAX_ROWS", 5000))
STREAM_APPEND_MAX_ROWS = int(getattr(settings, "DATASET_STREAM_APPEND_MAX_ROWS", 2000))
STREAM_QUERY_SOURCE_MAX_ROWS = int(getattr(settings, "DATASET_STREAM_QUERY_SOURCE_MAX_ROWS", 50000))
STREAM_QUERY_MIN_INTERVAL_SEC = float(getattr(settings, "DATASET_STREAM_QUERY_MIN_INTERVAL_SEC", 2.0))


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _scope_user_is_authenticated(scope) -> bool:
    user = scope.get("user")
    return bool(user and getattr(user, "is_authenticated", False))


def _dataset_stream_message(dataset: Dataset, payload: dict, message_type: str):
    return {
        "type": message_type,
        "rid": dataset.experiment_run_id,
        "dataset_id": str(dataset.id),
        "name": dataset.name,
        "columns": payload.get("columns", []),
        "data": payload.get("data", []),
        "rows": payload.get("rows", []),
        "truncated": bool(payload.get("truncated", False)),
        "row_count": payload.get("row_count"),
        "sent_rows": payload.get("sent_rows"),
        "total_rows": payload.get("total_rows"),
        "returned_rows": payload.get("returned_rows"),
        "start_row": payload.get("start_row"),
        "end_row": payload.get("end_row"),
        "updated_at": _now_iso(),
    }


def _tail_rows(rows, limit: int):
    if not isinstance(rows, list):
        return rows, False, None, None
    row_count = len(rows)
    if limit <= 0 or row_count <= limit:
        return rows, False, row_count, row_count
    return rows[-limit:], True, row_count, limit


def _limit_table_payload(payload: dict, key: str, limit: int):
    original_rows = payload.get(key, [])
    rows, truncated, row_count, sent_rows = _tail_rows(original_rows, limit)
    total_rows = payload.get("total_rows")
    if total_rows is None:
        total_rows = row_count
    end_row = payload.get("end_row")
    start_row = payload.get("start_row")
    if isinstance(original_rows, list) and isinstance(rows, list) and len(rows) != len(original_rows):
        end_row = end_row if end_row is not None else row_count
        start_row = max(0, int(end_row or 0) - len(rows))
    returned_rows = len(rows) if isinstance(rows, list) else sent_rows
    return {
        **payload,
        key: rows,
        "truncated": bool(payload.get("truncated", False) or truncated),
        "row_count": total_rows,
        "sent_rows": returned_rows,
        "total_rows": total_rows,
        "returned_rows": returned_rows,
        "start_row": start_row,
        "end_row": end_row,
    }


def _load_h5_table(file_path: str, metadata: dict | None = None, limit_rows: int | None = None):
    table, _meta = load_h5_point_table(file_path, metadata or {}, limit_rows=limit_rows)
    return table

@database_sync_to_async
def _fetch_runs(filters):
    qs = ExperimentRun.objects.all()
    status_q = filters.get("status")
    author_id = filters.get("author_id")
    limit = filters.get("limit")
    if status_q:
        statuses = [s.strip().upper() for s in str(status_q).split(",") if s.strip()]
        if statuses:
            qs = qs.filter(status__in=statuses)
    if author_id:
        try:
            qs = qs.filter(user_id=int(author_id))
        except Exception:
            pass
    qs = qs.order_by("-created_at")
    if limit:
        try:
            qs = qs[: int(limit)]
        except Exception:
            pass
    serializer = ExperimentRunListSerializer(qs, many=True)
    return {"items": serializer.data, "next_cursor": None}


@database_sync_to_async
def _fetch_run_status(run_id):
    run = (
        ExperimentRun.objects.filter(id=run_id)
        .values("id", "status", "started_at", "completed_at", "last_heartbeat_at")
        .first()
    )
    if run is None:
        return None
    status = run["status"]
    progress = 1.0 if status in {"COMPLETED", "FAILED", "CANCELLED", "ABORTED"} else 0.0
    return {
        "type": "status",
        "rid": run["id"],
        "status": status,
        "progress": progress,
        "started_at": run["started_at"].isoformat() if run["started_at"] else None,
        "completed_at": run["completed_at"].isoformat() if run["completed_at"] else None,
        "last_heartbeat_at": run["last_heartbeat_at"].isoformat() if run["last_heartbeat_at"] else None,
        "updated_at": _now_iso(),
    }


@database_sync_to_async
def _fetch_run_logs(run_id, after_id=None, limit=100):
    qs = RunLog.objects.filter(experiment_run_id=run_id).order_by("id")
    if after_id:
        qs = qs.filter(id__gt=after_id)
    logs = qs[:limit]
    return [
        {
            "id": log.id,
            "level": log.level,
            "message": log.message,
            "timestamp": log.timestamp.isoformat(),
        }
        for log in logs
    ]


class RunsListConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        if not _scope_user_is_authenticated(self.scope):
            await self.close(code=4401)
            return
        self.period_ms = 2000
        self._running = True
        self._filters = {}
        await self.accept()
        self._task = asyncio.create_task(self._loop())

    async def disconnect(self, close_code):
        self._running = False
        task = getattr(self, "_task", None)
        if task:
            task.cancel()

    async def receive(self, text_data=None, bytes_data=None):
        try:
            data = json.loads(text_data or "{}")
        except Exception:
            return
        if "period_ms" in data:
            try:
                self.period_ms = max(250, int(data["period_ms"]))
            except Exception:
                pass
        if "filters" in data and isinstance(data["filters"], dict):
            self._filters = data["filters"]
        if data.get("op") == "refresh":
            await self._send_snapshot()

    async def _send_snapshot(self):
        payload = await _fetch_runs(self._filters)
        msg = {
            "type": "runs",
            "items": payload.get("items", []),
            "next_cursor": payload.get("next_cursor"),
            "updated_at": _now_iso(),
        }
        await self.send(text_data=json.dumps(msg))

    async def _loop(self):
        while self._running:
            await asyncio.sleep(self.period_ms / 1000.0)
            await self._send_snapshot()


class RunStatusConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        if not _scope_user_is_authenticated(self.scope):
            await self.close(code=4401)
            return
        self.run_id = int(self.scope["url_route"]["kwargs"]["run_id"])
        self.period_ms = 500
        self._running = True
        await self.accept()
        await self._send_status()
        self._task = asyncio.create_task(self._loop())

    async def disconnect(self, close_code):
        self._running = False
        task = getattr(self, "_task", None)
        if task:
            task.cancel()

    async def receive(self, text_data=None, bytes_data=None):
        try:
            data = json.loads(text_data or "{}")
        except Exception:
            return
        if "period_ms" in data:
            try:
                self.period_ms = max(100, int(data["period_ms"]))
            except Exception:
                pass
        if data.get("op") == "refresh":
            await self._send_status()

    async def _loop(self):
        while self._running:
            await asyncio.sleep(self.period_ms / 1000.0)
            await self._send_status()

    async def _send_status(self):
        msg = await _fetch_run_status(self.run_id)
        if msg is None:
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "error",
                        "rid": self.run_id,
                        "message": "Run not found.",
                        "updated_at": _now_iso(),
                    }
                )
            )
            await self.close()
            return
        await self.send(text_data=json.dumps(msg))


class RunLogConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        if not _scope_user_is_authenticated(self.scope):
            await self.close(code=4401)
            return
        self.run_id = int(self.scope["url_route"]["kwargs"]["run_id"])
        self.period_ms = 600
        self._running = True
        self._last_log_id = 0
        await self.accept()
        await self._send_logs(initial=True)
        self._task = asyncio.create_task(self._loop())

    async def disconnect(self, close_code):
        self._running = False
        task = getattr(self, "_task", None)
        if task:
            task.cancel()

    async def receive(self, text_data=None, bytes_data=None):
        try:
            data = json.loads(text_data or "{}")
        except Exception:
            return
        if "period_ms" in data:
            try:
                self.period_ms = max(100, int(data["period_ms"]))
            except Exception:
                pass
        if data.get("op") == "refresh":
            self._last_log_id = 0
            await self._send_logs(initial=True)

    async def _loop(self):
        while self._running:
            await asyncio.sleep(self.period_ms / 1000.0)
            await self._send_logs()

    async def _send_logs(self, initial=False):
        logs = await _fetch_run_logs(self.run_id, None if initial else self._last_log_id)
        if logs:
            self._last_log_id = max(item["id"] for item in logs)
        await self.send(
            text_data=json.dumps(
                {
                    "type": "logs",
                    "rid": self.run_id,
                    "items": logs,
                    "updated_at": _now_iso(),
                }
            )
        )


class DatasetStreamConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        if not _scope_user_is_authenticated(self.scope):
            await self.close(code=4401)
            return
        self.run_id = int(self.scope["url_route"]["kwargs"]["run_id"])
        self.dataset_ref = self.scope["url_route"]["kwargs"]["dataset_id"]
        self.group_name = f"run_{self.run_id}"
        self.query_text = None
        self._query_snapshot_pending = False
        self._last_query_snapshot_at = 0.0
        self._running = True
        self.dataset = await _resolve_dataset(self.run_id, self.dataset_ref)
        await self.accept()
        if self.dataset is None:
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "error",
                        "rid": self.run_id,
                        "dataset_id": self.dataset_ref,
                        "message": "Dataset not found.",
                        "updated_at": _now_iso(),
                    }
                )
            )
            await self.close()
            return
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self._send_current_snapshot()

    async def disconnect(self, close_code):
        self._running = False
        dataset = getattr(self, "dataset", None)
        if dataset is not None:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        try:
            data = json.loads(text_data or "{}")
        except Exception:
            return

        if data.get("op") == "refresh":
            await self._send_current_snapshot()
            return

        if data.get("op") == "reset_query":
            self.query_text = None
            await self._send_current_snapshot()
            return

        query_text = str(data.get("query") or "").strip()
        if query_text:
            self.query_text = query_text
            await self._send_current_snapshot()

    async def _send_current_snapshot(self):
        dataset = getattr(self, "dataset", None)
        if dataset is None:
            return
        if self.query_text:
            try:
                payload = await _load_dataset_query_table(dataset.id, self.query_text)
            except DatasetQueryError as exc:
                await self.send(
                    text_data=json.dumps(
                        {
                            "type": "error",
                            "rid": dataset.experiment_run_id,
                            "dataset_id": str(dataset.id),
                            "name": dataset.name,
                            "message": str(exc),
                            "updated_at": _now_iso(),
                        }
                    )
                )
                return
        else:
            payload = await _load_dataset_table(dataset.id, STREAM_SNAPSHOT_MAX_ROWS)

        if payload is None:
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "error",
                        "rid": dataset.experiment_run_id,
                        "dataset_id": str(dataset.id),
                        "name": dataset.name,
                        "message": "Dataset could not be loaded.",
                        "updated_at": _now_iso(),
                    }
                )
            )
            return

        payload = _limit_table_payload(payload, "data", STREAM_SNAPSHOT_MAX_ROWS)
        await self.send(text_data=json.dumps(_dataset_stream_message(dataset, payload, "snapshot")))

    async def _send_query_snapshot_later(self):
        if self._query_snapshot_pending:
            return
        self._query_snapshot_pending = True
        try:
            elapsed = asyncio.get_running_loop().time() - self._last_query_snapshot_at
            delay = max(0.0, STREAM_QUERY_MIN_INTERVAL_SEC - elapsed)
            if delay:
                await asyncio.sleep(delay)
            if self._running and self.query_text:
                await self._send_current_snapshot()
                self._last_query_snapshot_at = asyncio.get_running_loop().time()
        finally:
            self._query_snapshot_pending = False

    async def new_data(self, event):
        dataset = getattr(self, "dataset", None)
        if dataset is None:
            return
        event_dataset_id = str(event.get("dataset_id", ""))
        if event_dataset_id and event_dataset_id != str(dataset.id):
            return
        dataset_name = event.get("dataset_name")
        if dataset_name and dataset_name != dataset.name:
            return
        rows = event.get("rows")
        columns = event.get("columns")
        if not isinstance(rows, list) or not isinstance(columns, list):
            return
        if self.query_text:
            asyncio.create_task(self._send_query_snapshot_later())
            return
        payload = _limit_table_payload({"columns": columns, "rows": rows}, "rows", STREAM_APPEND_MAX_ROWS)
        await self.send(
            text_data=json.dumps(
                _dataset_stream_message(
                    dataset,
                    payload,
                    "append",
                )
            )
        )


@database_sync_to_async
def _resolve_dataset(run_id: int, dataset_ref: str):
    qs = Dataset.objects.filter(experiment_run_id=run_id)
    try:
        dataset_id = int(dataset_ref)
    except Exception:
        dataset_id = None
    if dataset_id is not None:
        ds = qs.filter(id=dataset_id).order_by("-created_at").first()
        if ds is not None:
            return ds
    return qs.filter(name=dataset_ref).order_by("-created_at").first()


@database_sync_to_async
def _load_dataset_table(dataset_id: int, limit_rows: int | None = None):
    dataset = Dataset.objects.select_related("experiment_run").filter(id=dataset_id).first()
    if dataset is None:
        return None
    try:
        return _load_h5_table(dataset.file_path, dataset.metadata or {}, limit_rows=limit_rows)
    except (OSError, KeyError, ValueError, h5py.H5Error):
        return None


@database_sync_to_async
def _load_dataset_query_table(dataset_id: int, query_text: str):
    dataset = Dataset.objects.select_related("experiment_run").filter(id=dataset_id).first()
    if dataset is None:
        return None
    try:
        table = _load_h5_table(dataset.file_path, dataset.metadata or {}, limit_rows=STREAM_QUERY_SOURCE_MAX_ROWS)
        result = execute_query(table.get("columns", []), table.get("data", []), query_text)
        return _limit_table_payload({
            "columns": result.get("columns", []),
            "data": result.get("data", []),
        }, "data", STREAM_SNAPSHOT_MAX_ROWS)
    except DatasetQueryError:
        raise
    except (OSError, KeyError, ValueError, h5py.H5Error):
        return None


class TTLStatusConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        if not _scope_user_is_authenticated(self.scope):
            await self.close(code=4401)
            return
        self.period_ms = 1000
        self._running = True
        self._tick = 0
        await self.accept()
        self._task = asyncio.create_task(self._loop())

    async def disconnect(self, close_code):
        self._running = False
        task = getattr(self, "_task", None)
        if task:
            task.cancel()

    async def receive(self, text_data=None, bytes_data=None):
        try:
            data = json.loads(text_data or "{}")
        except Exception:
            return
        if "period_ms" in data:
            try:
                self.period_ms = max(100, int(data["period_ms"]))
            except Exception:
                pass

    async def _loop(self):
        while self._running:
            await asyncio.sleep(self.period_ms / 1000.0)
            self._tick += 1
            items = []
            for name in TTL_DEVICES:
                state = TTL_STATE.get(name, {})
                level = state.get("level", "LOW")
                override = bool(state.get("override", False))
                value = state.get("value", level == "HIGH")
                if not override:
                    if self._tick % 5 == 0:
                        value = random.choice([True, False])
                items.append({
                    "device": name,
                    "level": level,
                    "override": override,
                    "value": value,
                })
            await self.send(text_data=json.dumps({"type": "ttl_status", "items": items}))

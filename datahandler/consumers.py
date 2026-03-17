"""
WebSocket consumers for the datahandler app.
Handles real-time communication for run status, logs, and dataset streams.
"""
import json
import asyncio
import random
import datetime
import h5py
import numpy as np
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from core.ttl_state import TTL_DEVICES, TTL_STATE
from expmanager.models import Dataset, ExperimentRun
from expmanager.dataset_payloads import dataset_to_point_rows, open_h5_read
from expmanager.serializers import ExperimentRunListSerializer


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _dataset_stream_message(dataset: Dataset, payload: dict, message_type: str):
    return {
        "type": message_type,
        "rid": dataset.experiment_run_id,
        "dataset_id": str(dataset.id),
        "name": dataset.name,
        "columns": payload.get("columns", []),
        "data": payload.get("data", []),
        "rows": payload.get("rows", []),
        "updated_at": _now_iso(),
    }

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


class RunsListConsumer(AsyncWebsocketConsumer):
    async def connect(self):
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
        self.run_id = int(self.scope["url_route"]["kwargs"]["run_id"])
        self.period_ms = 500
        self._running = True
        # This consumer currently simulates status updates.
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
        statuses = ["QUEUED", "RUNNING", "RUNNING", "RUNNING", "COMPLETED"]
        progress = 0.0
        while self._running:
            await asyncio.sleep(self.period_ms / 1000.0)
            status = random.choice(statuses)
            progress = min(1.0, progress + random.uniform(0.01, 0.08))
            msg = {
                "type": "status",
                "rid": self.run_id,
                "status": status,
                "progress": round(progress, 3),
                "updated_at": _now_iso(),
            }
            await self.send(text_data=json.dumps(msg))


class RunLogConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.run_id = int(self.scope["url_route"]["kwargs"]["run_id"])
        self.period_ms = 600
        self._running = True
        # This consumer currently simulates log output.
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
        levels = ["INFO", "WARN", "DEBUG"]
        messages = [
            "Initializing pulse program.",
            "DAQ buffer updated.",
            "Temperature stable.",
            "Calibration step complete.",
            "Waiting for hardware response.",
        ]
        while self._running:
            await asyncio.sleep(self.period_ms / 1000.0)
            msg = {
                "type": "log",
                "rid": self.run_id,
                "level": random.choice(levels),
                "message": random.choice(messages),
                "ts": _now_iso(),
            }
            await self.send(text_data=json.dumps(msg))


class DatasetStreamConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.run_id = int(self.scope["url_route"]["kwargs"]["run_id"])
        self.dataset_ref = self.scope["url_route"]["kwargs"]["dataset_id"]
        self.group_name = f"run_{self.run_id}"
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
        snapshot = await _load_dataset_table(self.dataset.id)
        if snapshot is None:
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "error",
                        "rid": self.run_id,
                        "dataset_id": str(self.dataset.id),
                        "name": self.dataset.name,
                        "message": "Dataset could not be loaded.",
                        "updated_at": _now_iso(),
                    }
                )
            )
            await self.close()
            return
        await self.send(text_data=json.dumps(_dataset_stream_message(self.dataset, snapshot, "snapshot")))

    async def disconnect(self, close_code):
        dataset = getattr(self, "dataset", None)
        if dataset is not None:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        return

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
        await self.send(
            text_data=json.dumps(
                _dataset_stream_message(
                    dataset,
                    {"columns": columns, "rows": rows},
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
def _load_dataset_table(dataset_id: int):
    dataset = Dataset.objects.select_related("experiment_run").filter(id=dataset_id).first()
    if dataset is None:
        return None
    try:
        with open_h5_read(dataset.file_path) as f:
            data_arr = np.array(f["data"]) if "data" in f else np.array([])
            params_arr = np.array(f["params"]) if "params" in f else None
            file_meta = {}
            for key in f.attrs.keys():
                value = f.attrs.get(key)
                if isinstance(value, np.ndarray):
                    file_meta[key] = value.tolist()
                else:
                    file_meta[key] = value
            combined_meta = dict(dataset.metadata or {})
            combined_meta.update(file_meta)
            return dataset_to_point_rows(data_arr, params_arr, combined_meta)
    except (OSError, KeyError, ValueError, h5py.H5Error):
        return None


class TTLStatusConsumer(AsyncWebsocketConsumer):
    async def connect(self):
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

"""
WebSocket consumers for the datahandler app.
Handles real-time communication for run status, logs, and dataset streams.
"""
import json
import asyncio
import random
import datetime
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from core.ttl_state import TTL_DEVICES, TTL_STATE
from expmanager.models import ExperimentRun
from expmanager.serializers import ExperimentRunListSerializer


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")

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
        self.dataset_id = self.scope["url_route"]["kwargs"]["dataset_id"]
        self.period_ms = 200
        self._running = True
        self._index = 0
        self._op = "append"
        # This consumer currently streams synthetic points.
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
                self.period_ms = max(50, int(data["period_ms"]))
            except Exception:
                pass
        if data.get("op") in ("append", "replace", "reset"):
            self._op = data["op"]
        if data.get("mode") == "reset":
            self._index = 0

    async def _loop(self):
        while self._running:
            await asyncio.sleep(self.period_ms / 1000.0)
            now = _now_iso()
            if self._op == "reset":
                msg = {
                    "type": "patch",
                    "op": "reset",
                    "rid": self.run_id,
                    "dataset_id": self.dataset_id,
                    "updated_at": now,
                }
                self._index = 0
                await self.send(text_data=json.dumps(msg))
                continue

            points = []
            for _ in range(3):
                points.append({
                    "index": self._index,
                    "x": self._index,
                    "y": random.random(),
                })
                self._index += 1

            if self._op == "replace":
                msg = {
                    "type": "patch",
                    "op": "replace",
                    "rid": self.run_id,
                    "dataset_id": self.dataset_id,
                    "data": points,
                    "updated_at": now,
                }
            else:
                msg = {
                    "type": "patch",
                    "op": "append",
                    "rid": self.run_id,
                    "dataset_id": self.dataset_id,
                    "points": points,
                    "updated_at": now,
                }
            await self.send(text_data=json.dumps(msg))


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

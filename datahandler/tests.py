from asgiref.sync import async_to_sync
from django.contrib.auth.models import User
from django.test import SimpleTestCase
from django.test import TestCase

from expmanager.models import ExperimentRun, RunLog

from .consumers import _fetch_run_logs, _fetch_run_status, _limit_table_payload


class DatasetStreamPayloadTests(SimpleTestCase):
    def test_limits_snapshot_rows_to_tail(self):
        payload = {"columns": ["x"], "data": [[i] for i in range(5)]}

        limited = _limit_table_payload(payload, "data", 3)

        self.assertEqual(limited["data"], [[2], [3], [4]])
        self.assertTrue(limited["truncated"])
        self.assertEqual(limited["row_count"], 5)
        self.assertEqual(limited["sent_rows"], 3)

    def test_keeps_small_append_payloads_unchanged(self):
        payload = {"columns": ["x"], "rows": [[1], [2]]}

        limited = _limit_table_payload(payload, "rows", 3)

        self.assertEqual(limited["rows"], [[1], [2]])
        self.assertFalse(limited["truncated"])
        self.assertEqual(limited["row_count"], 2)
        self.assertEqual(limited["sent_rows"], 2)

    def test_limits_preserve_total_window_metadata(self):
        payload = {
            "columns": ["x"],
            "data": [[i] for i in range(5)],
            "total_rows": 20,
            "returned_rows": 5,
            "start_row": 10,
            "end_row": 15,
            "truncated": True,
        }

        limited = _limit_table_payload(payload, "data", 3)

        self.assertEqual(limited["data"], [[2], [3], [4]])
        self.assertTrue(limited["truncated"])
        self.assertEqual(limited["row_count"], 20)
        self.assertEqual(limited["total_rows"], 20)
        self.assertEqual(limited["returned_rows"], 3)
        self.assertEqual(limited["start_row"], 12)
        self.assertEqual(limited["end_row"], 15)


class RunWebsocketPayloadTests(TestCase):
    def test_fetch_run_status_uses_database_state(self):
        user = User.objects.create_user(username="ws-runner")
        run = ExperimentRun.objects.create(user=user, script_path="ws.qil", status="FAILED")

        payload = async_to_sync(_fetch_run_status)(run.id)

        self.assertEqual(payload["rid"], run.id)
        self.assertEqual(payload["status"], "FAILED")
        self.assertEqual(payload["progress"], 1.0)

    def test_fetch_run_logs_returns_only_newer_logs(self):
        user = User.objects.create_user(username="ws-logger")
        run = ExperimentRun.objects.create(user=user, script_path="ws.qil")
        first = RunLog.objects.create(experiment_run=run, level="INFO", message="first")
        RunLog.objects.create(experiment_run=run, level="ERROR", message="second")

        payload = async_to_sync(_fetch_run_logs)(run.id, after_id=first.id)

        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["level"], "ERROR")
        self.assertEqual(payload[0]["message"], "second")

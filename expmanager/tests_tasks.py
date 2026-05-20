from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
import datetime
from unittest.mock import patch

from .models import ExperimentRun, ExperimentSchedule, RunLog
from .tasks import mark_stale_running_runs, maybe_fail_schedule_after_repeated_failures, run_experiment_task
from orchestrator.instrument_client import DeviceConnectionError


class RunExperimentTaskTests(TestCase):
    def test_engine_startup_failure_marks_run_failed(self):
        user = User.objects.create_user(username="runner", password="secret")
        run = ExperimentRun.objects.create(user=user, script_path="missing.qil")

        with patch("expmanager.tasks.ExperimentEngine", side_effect=ConnectionError("instrumentserver offline")):
            status = run_experiment_task.run(run.id)

        run.refresh_from_db()
        self.assertEqual(status, "FAILED")
        self.assertEqual(run.status, "FAILED")
        self.assertIsNotNone(run.started_at)
        self.assertIsNotNone(run.last_heartbeat_at)
        self.assertIsNotNone(run.completed_at)
        self.assertTrue(
            RunLog.objects.filter(
                experiment_run=run,
                level="ERROR",
                message__contains="instrumentserver offline",
            ).exists()
        )

    def test_stale_running_run_is_marked_failed(self):
        user = User.objects.create_user(username="stale-runner", password="secret")
        old_time = timezone.now() - datetime.timedelta(minutes=10)
        run = ExperimentRun.objects.create(
            user=user,
            script_path="stale.qil",
            status="RUNNING",
            started_at=old_time,
            last_heartbeat_at=old_time,
        )

        marked = mark_stale_running_runs(timeout_seconds=60)

        run.refresh_from_db()
        self.assertEqual(marked, 1)
        self.assertEqual(run.status, "FAILED")
        self.assertIsNotNone(run.completed_at)
        self.assertTrue(
            RunLog.objects.filter(
                experiment_run=run,
                level="ERROR",
                message__contains="heartbeat was lost",
            ).exists()
        )

    def test_fresh_running_run_is_not_marked_failed(self):
        user = User.objects.create_user(username="fresh-runner", password="secret")
        run = ExperimentRun.objects.create(
            user=user,
            script_path="fresh.qil",
            status="RUNNING",
            started_at=timezone.now(),
            last_heartbeat_at=timezone.now(),
        )

        marked = mark_stale_running_runs(timeout_seconds=60)

        run.refresh_from_db()
        self.assertEqual(marked, 0)
        self.assertEqual(run.status, "RUNNING")

    @override_settings(EXPERIMENT_SCHEDULE_MAX_CONSECUTIVE_FAILURES=2)
    def test_schedule_is_failed_after_repeated_run_failures(self):
        user = User.objects.create_user(username="schedule-runner", password="secret")
        schedule = ExperimentSchedule.objects.create(
            user=user,
            script_path="repeat.qil",
            schedule_type="RECURRING",
            recurrence={"kind": "interval", "interval_min": 1},
        )
        ExperimentRun.objects.create(user=user, script_path="repeat.qil", schedule=schedule, status="FAILED")
        current = ExperimentRun.objects.create(user=user, script_path="repeat.qil", schedule=schedule, status="FAILED")
        queued = ExperimentRun.objects.create(user=user, script_path="repeat.qil", schedule=schedule, status="QUEUED")

        stopped = maybe_fail_schedule_after_repeated_failures(current)

        schedule.refresh_from_db()
        queued.refresh_from_db()
        self.assertTrue(stopped)
        self.assertEqual(schedule.status, "FAILED")
        self.assertEqual(queued.status, "FAILED")
        self.assertTrue(
            RunLog.objects.filter(
                experiment_run=current,
                message__contains="Schedule stopped",
            ).exists()
        )

    def test_device_connection_loss_marks_run_failed(self):
        user = User.objects.create_user(username="device-runner", password="secret")
        run = ExperimentRun.objects.create(user=user, script_path="device.qil")

        class FailingEngine:
            def __init__(self, experiment_run):
                self.experiment_run = experiment_run

            def run(self):
                raise DeviceConnectionError("sequencer usb link lost", target="sequencer.run")

            def log(self, message, level="INFO"):
                RunLog.objects.create(experiment_run=self.experiment_run, level=level, message=message)

        with patch("expmanager.tasks.ExperimentEngine", FailingEngine):
            status = run_experiment_task.run(run.id)

        run.refresh_from_db()
        self.assertEqual(status, "FAILED")
        self.assertEqual(run.status, "FAILED")
        self.assertTrue(
            RunLog.objects.filter(
                experiment_run=run,
                level="ERROR",
                message__contains="DEVICE_DISCONNECTED",
            ).exists()
        )

from django.test import SimpleTestCase, override_settings
from unittest.mock import Mock

from .engine import ExperimentEngine


class ExperimentEngineSleepTests(SimpleTestCase):
    def test_interruptible_sleep_checks_abort(self):
        engine = ExperimentEngine.__new__(ExperimentEngine)
        engine.check_abort = Mock()

        engine.interruptible_sleep(0.001)

        self.assertTrue(engine.check_abort.called)

    def test_interruptible_sleep_raises_when_aborted(self):
        engine = ExperimentEngine.__new__(ExperimentEngine)
        engine.check_abort = Mock(side_effect=InterruptedError("aborted"))

        with self.assertRaises(InterruptedError):
            engine.interruptible_sleep(10)


class ExperimentEngineFlushTests(SimpleTestCase):
    @override_settings(EXPERIMENT_DATA_FLUSH_EVERY_SAVES=1)
    def test_default_safe_flushes_every_save(self):
        engine = ExperimentEngine.__new__(ExperimentEngine)

        self.assertTrue(engine._should_flush_save(1))
        self.assertTrue(engine._should_flush_save(2))

    @override_settings(EXPERIMENT_DATA_FLUSH_EVERY_SAVES=10)
    def test_flush_interval_is_configurable(self):
        engine = ExperimentEngine.__new__(ExperimentEngine)

        self.assertFalse(engine._should_flush_save(9))
        self.assertTrue(engine._should_flush_save(10))

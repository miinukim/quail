from django.test import SimpleTestCase

from .instrument_client import (
    DeviceConnectionError,
    DeviceTimeoutError,
    SequencerNotReadyError,
    instrument_error_from_payload,
)


class InstrumentErrorContractTests(SimpleTestCase):
    def test_structured_device_disconnect_error_maps_to_device_exception(self):
        exc = instrument_error_from_payload(
            {"code": "DEVICE_DISCONNECTED", "message": "sequencer usb link lost"},
            operation="call",
            target="sequencer.run",
        )

        self.assertIsInstance(exc, DeviceConnectionError)
        self.assertEqual(exc.code, "DEVICE_DISCONNECTED")
        self.assertEqual(exc.operation, "call")
        self.assertEqual(exc.target, "sequencer.run")
        self.assertIn("sequencer usb link lost", str(exc))

    def test_string_timeout_error_maps_to_timeout_exception(self):
        exc = instrument_error_from_payload("sequencer timed out while waiting for trigger")

        self.assertIsInstance(exc, DeviceTimeoutError)
        self.assertEqual(exc.code, "DEVICE_TIMEOUT")

    def test_string_sequencer_not_ready_error_maps_to_sequencer_exception(self):
        exc = instrument_error_from_payload("sequencer is not ready")

        self.assertIsInstance(exc, SequencerNotReadyError)
        self.assertEqual(exc.code, "SEQUENCER_NOT_READY")

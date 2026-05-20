"""
InstrumentClient acts as a proxy for communicating with the Instrument Server.
It uses ZeroMQ to send commands (get/set parameters, call methods) to the server.
"""
import zmq
import json
import logging

logger = logging.getLogger("quail.engine.instrument")


class InstrumentError(RuntimeError):
    """Base class for instrumentserver/device failures reported to Quail."""

    code = "INSTRUMENT_ERROR"

    def __init__(self, message, *, target=None, operation=None, details=None):
        self.target = target
        self.operation = operation
        self.details = details
        super().__init__(f"{self.code}: {message}")


class InstrumentServerError(InstrumentError):
    code = "INSTRUMENTSERVER_ERROR"


class DeviceConnectionError(InstrumentError):
    code = "DEVICE_DISCONNECTED"


class DeviceTimeoutError(InstrumentError):
    code = "DEVICE_TIMEOUT"


class SequencerNotReadyError(InstrumentError):
    code = "SEQUENCER_NOT_READY"


ERROR_CODE_TO_EXCEPTION = {
    "DEVICE_DISCONNECTED": DeviceConnectionError,
    "DEVICE_CONNECTION_LOST": DeviceConnectionError,
    "DEVICE_TIMEOUT": DeviceTimeoutError,
    "SEQUENCER_TIMEOUT": DeviceTimeoutError,
    "SEQUENCER_NOT_READY": SequencerNotReadyError,
}


def _error_payload_message(error_payload):
    if isinstance(error_payload, dict):
        return str(error_payload.get("message") or error_payload.get("error") or error_payload.get("code") or "Instrument failure")
    return str(error_payload)


def _error_payload_code(error_payload):
    if isinstance(error_payload, dict):
        code = error_payload.get("code") or error_payload.get("type")
        return str(code).upper() if code else ""
    text = str(error_payload).lower()
    if any(marker in text for marker in ("disconnect", "connection lost", "not connected", "device offline", "no route to host")):
        return "DEVICE_DISCONNECTED"
    if "timeout" in text or "timed out" in text:
        return "DEVICE_TIMEOUT"
    if "sequencer" in text and any(marker in text for marker in ("not ready", "not armed", "not initialized")):
        return "SEQUENCER_NOT_READY"
    return ""


def instrument_error_from_payload(error_payload, *, operation=None, target=None):
    code = _error_payload_code(error_payload)
    exc_cls = ERROR_CODE_TO_EXCEPTION.get(code, InstrumentServerError)
    return exc_cls(
        _error_payload_message(error_payload),
        operation=operation,
        target=target,
        details=error_payload if isinstance(error_payload, dict) else None,
    )


class InstrumentClient:
    """
    ZMQ Client for instrumentserver.
    """
    def __init__(self, host="127.0.0.1", port=5555):
        self.host = host
        self.port = port
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REQ)
        logger.info("InstrumentClient connecting to tcp://%s:%s", self.host, self.port)
        self.socket.connect(f"tcp://{self.host}:{self.port}")
        self.socket.setsockopt(zmq.RCVTIMEO, 5000)
        self.socket.setsockopt(zmq.SNDTIMEO, 5000)

    def close(self):
        """Release ZMQ resources for this client."""
        try:
            if getattr(self, "socket", None) is not None:
                self.socket.close(linger=0)
        finally:
            if getattr(self, "context", None) is not None:
                self.context.term()

    def health_check(self):
        """
        Active connectivity check.
        Sends a lightweight request and expects a valid server response.
        """
        instruction = {
            "operation": "get_existing_instruments",
            "_class_type": "ServerInstruction",
        }
        return self._send(instruction)

    def _send(self, instruction_dict):
        """
        Send a raw dictionary as a JSON string and wait for response.
        """
        operation = instruction_dict.get("operation")
        target = (instruction_dict.get("call_spec") or {}).get("target")
        try:
            logger.info("InstrumentClient send: %s target=%s", operation, target)
            self.socket.send_string(json.dumps(instruction_dict))
            response_str = self.socket.recv_string()
            response = json.loads(response_str)
            logger.info("InstrumentClient recv: class=%s error=%s", response.get("_class_type"), response.get("error"))
            
            if response.get("_class_type") == "ServerResponse":
                if response.get("error") and response["error"] != "None":
                    raise instrument_error_from_payload(response["error"], operation=operation, target=target)
                return response.get("message")
            return response
            
        except zmq.ZMQError as e:
            logger.error(f"ZMQ Error: {e}")
            raise InstrumentServerError(f"Communication failed: {e}", operation=operation, target=target)

    def get_parameter(self, instrument, parameter):
        """
        Get a parameter value.
        """
        # Construct full path
        if instrument:
            full_path = f"{instrument}.{parameter}"
        else:
            full_path = parameter

        # Operation.get_param_dict
        instruction = {
            "operation": "get_param_dict",
            "serialization_opts": {
                "path": full_path,
                "attrs": ["value"],
                "args": [],
                "kwargs": {},
                "_class_type": "ParameterSerializeSpec"
            },
            "_class_type": "ServerInstruction"
        }
        
        result = self._send(instruction)
        
        # The result from instrumentserver for get_param_dict is typically a nested dictionary
        # matching the structure of the requested path.
        if isinstance(result, dict):
            if full_path in result:
                return result[full_path]
            
            # If it's nested {instrument: {param: value}}
            parts = full_path.split('.')
            curr = result
            try:
                for part in parts:
                    curr = curr[part]
                return curr
            except (KeyError, TypeError):
                pass
                
        return result

    def set_parameter(self, instrument, parameter, value):
        """
        Set a parameter value.
        """
        # Construct full path
        if instrument:
            full_path = f"{instrument}.{parameter}"
        else:
            full_path = parameter

        # Operation.set_params
        instruction = {
            "operation": "set_params",
            "set_parameters": {
                full_path: value
            },
            "_class_type": "ServerInstruction"
        }
        return self._send(instruction)

    def call(self, target, *args, **kwargs):
        """
        Call a method on an instrument.
        """
        instruction = {
            "operation": "call",
            "call_spec": {
                "target": target,
                "args": list(args),
                "kwargs": kwargs,
                "_class_type": "CallSpec"
            },
            "_class_type": "ServerInstruction"
        }
        return self._send(instruction)

    def upload_fpga_code(self, device_name, code):
        """
        Upload FPGA code.
        """
        return self.call(f"{device_name}.load_program", code)

    def trigger_fpga(self, device_name):
        """
        Trigger the FPGA.
        """
        return self.call(f"{device_name}.start_sequencer")

    def stop_fpga(self, device_name):
        """
        Stop the FPGA.
        """
        return self.call(f"{device_name}.stop_sequencer")

    def run_fpga(self, device_name, code):
        """
        Run the FPGA with the given code.
        """
        return self.call(f"{device_name}.run", code)

"""
InstrumentClient acts as a proxy for communicating with the Instrument Server.
It uses ZeroMQ to send commands (get/set parameters, call methods) to the server.
"""
import zmq
import json
import logging

logger = logging.getLogger(__name__)

class InstrumentClient:
    """
    ZMQ Client for instrumentserver.
    """
    def __init__(self, host="127.0.0.1", port=5555):
        self.host = host
        self.port = port
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REQ)
        self.socket.connect(f"tcp://{self.host}:{self.port}")
        self.socket.setsockopt(zmq.RCVTIMEO, 5000)
        self.socket.setsockopt(zmq.SNDTIMEO, 5000)

    def _send(self, instruction_dict):
        """
        Send a raw dictionary as a JSON string and wait for response.
        """
        try:
            self.socket.send_string(json.dumps(instruction_dict))
            response_str = self.socket.recv_string()
            response = json.loads(response_str)
            
            if response.get("_class_type") == "ServerResponse":
                if response.get("error") and response["error"] != "None":
                    raise RuntimeError(f"Server Error: {response['error']}")
                return response.get("message")
            return response
            
        except zmq.ZMQError as e:
            logger.error(f"ZMQ Error: {e}")
            raise ConnectionError(f"Communication failed: {e}")

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

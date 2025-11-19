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
        # Operation.get_param_dict
        instruction = {
            "operation": "get_param_dict",
            "serialization_opts": {
                "path": instrument,
                "attrs": ["value"],
                "args": [],
                "kwargs": {},
                "_class_type": "ParameterSerializeSpec"
            },
            "_class_type": "ServerInstruction"
        }
        
        result = self._send(instruction)
        # Result is typically {param_name: value} or nested
        # Based on user code: self.buffer.extend(pdict[param])
        if isinstance(result, dict) and parameter in result:
            return result[parameter]
        return result

    def set_parameter(self, instrument, parameter, value):
        """
        Set a parameter value.
        """
        # Operation.set_params
        instruction = {
            "operation": "set_params",
            "set_parameters": {
                parameter: value
            },
            "_class_type": "ServerInstruction"
        }
        # Note: set_parameters usually takes full path or dict? 
        # User code: await self._set_parameters({param: setto}) where param is likely "instrument.param"
        # If 'parameter' arg is just the name, we might need to qualify it.
        # Assuming 'parameter' passed here is fully qualified or unique enough.
        # If instrument is passed, maybe we should combine them?
        # Let's assume parameter is "instrument.param" or just "param" if unique.
        # But the user code had: instrument = command["instrument"], param = command["param"], setto = ...
        # And called _set_parameters({param: setto}). 
        # So let's try to combine if needed. For now, I'll assume parameter is the full name or the server handles it.
        
        # Actually, looking at user code:
        # elif cmd_type == "param":
        #    instrument = command["instrument"]
        #    param = command.get("param_name")
        #    ...
        #    await self._set_parameters({param: setto})
        
        # It seems 'param' might be the full path in the user's config.
        # I will construct the dict as {parameter: value}.
        
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

    def upload_fpga_code(self, code):
        """
        Upload FPGA code.
        Maps to 'artys7.load_program'.
        """
        return self.call("artys7.load_program", code)

    def trigger_fpga(self):
        """
        Trigger the FPGA.
        Maps to 'artys7.start_sequencer'.
        """
        return self.call("artys7.start_sequencer")

    def stop_fpga(self):
        """
        Stop the FPGA.
        Maps to 'artys7.stop_sequencer'.
        """
        return self.call("artys7.stop_sequencer")

    def run_fpga(self, code):
        """
        Run the FPGA with the given code.
        Maps to 'artys7.run'.
        """
        return self.call("artys7.run", code)

#!/usr/bin/env python3
import argparse
import hashlib
import json
import logging
import math
from copy import deepcopy

import zmq


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


DEFAULT_STATE = {
    "dac.threshold": 3.0,
    "dac.voltage": 0.0,
    "device.freq": 100.0,
    "device.gain": 1.0,
    "device2.voltage": 0.125,
    "keithley.current": 1.2e-6,
    "multimeter.current": 2.4e-6,
    "sequencer.sample_rate": 250_000_000.0,
    "sequencer.readout_freq": 55_000_000.0,
    "sequencer.readout_gain": 1.0,
    "seq1.sample_rate": 250_000_000.0,
    "seq1.readout_freq": 55_000_000.0,
    "seq1.readout_gain": 1.0,
}


class MockInstrumentServer:
    def __init__(self, host="*", port=5555):
        self.host = host
        self.port = port
        self.state = deepcopy(DEFAULT_STATE)
        self.programs = {}
        self.run_counts = {}
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REP)
        self.socket.bind(f"tcp://{self.host}:{self.port}")
        logger.info("Mock InstrumentServer listening on tcp://%s:%s", self.host, self.port)

    def close(self):
        try:
            self.socket.close(linger=0)
        finally:
            self.context.term()

    def serve_forever(self):
        while True:
            try:
                request_raw = self.socket.recv_string()
                logger.info("Received request: %s", request_raw)
                response = self.handle_raw_request(request_raw)
                self.socket.send_string(json.dumps(response))
            except KeyboardInterrupt:
                logger.info("Stopping mock instrumentserver")
                break
            except Exception as exc:
                logger.exception("Unhandled mock instrumentserver error")
                self.socket.send_string(json.dumps(self._error_response(str(exc))))

        self.close()

    def handle_raw_request(self, request_raw):
        try:
            request = json.loads(request_raw)
        except json.JSONDecodeError:
            return self._error_response("Invalid JSON")

        operation = request.get("operation")
        if operation == "get_existing_instruments":
            return self._ok_response(self._existing_instruments())
        if operation == "get_param_dict":
            return self._ok_response(self._handle_get_param_dict(request))
        if operation == "set_params":
            return self._ok_response(self._handle_set_params(request))
        if operation == "call":
            return self._ok_response(self._handle_call(request))
        return self._error_response(f"Unknown operation: {operation}")

    def _existing_instruments(self):
        names = {
            key.split(".", 1)[0]
            for key in self.state
            if "." in key
        }
        names.update(self.programs.keys())
        names.update({"dac", "device", "device2", "keithley", "multimeter", "sequencer", "seq1"})
        return sorted(names)

    def _handle_get_param_dict(self, request):
        spec = request.get("serialization_opts") or {}
        path = spec.get("path")
        if not path:
            return self._nested_full_state()

        if path in self.state:
            return self._nested_path_value(path, self.state[path])

        matching = {key: value for key, value in self.state.items() if key.startswith(f"{path}.")}
        if matching:
            return self._nested_from_mapping(matching)

        instrument_name = path.split(".", 1)[0]
        if instrument_name in self._existing_instruments():
            return self._nested_from_mapping({
                key: value for key, value in self.state.items() if key.startswith(f"{instrument_name}.")
            })

        raise KeyError(f"Unknown parameter path: {path}")

    def _handle_set_params(self, request):
        updates = request.get("set_parameters") or {}
        if not isinstance(updates, dict):
            raise ValueError("set_parameters must be a dictionary")

        for path, value in updates.items():
            self.state[path] = value
            logger.info("Set %s = %r", path, value)

        return updates

    def _handle_call(self, request):
        call_spec = request.get("call_spec") or {}
        target = call_spec.get("target") or ""
        args = call_spec.get("args") or []
        kwargs = call_spec.get("kwargs") or {}
        logger.info("Calling target=%s args=%r kwargs=%r", target, args, kwargs)

        if "." not in target:
            return {"target": target, "args": args, "kwargs": kwargs, "status": "ok"}

        device_name, method_name = target.split(".", 1)

        if method_name == "load_program":
            program = args[0] if args else ""
            self.programs[device_name] = str(program)
            return {"loaded": True, "device": device_name, "program_bytes": len(str(program))}

        if method_name == "start_sequencer":
            self.run_counts[device_name] = self.run_counts.get(device_name, 0) + 1
            return {"started": True, "device": device_name, "run_count": self.run_counts[device_name]}

        if method_name == "stop_sequencer":
            return {"stopped": True, "device": device_name}

        if method_name == "run":
            program = str(args[0]) if args else self.programs.get(device_name, "")
            self.programs[device_name] = program
            self.run_counts[device_name] = self.run_counts.get(device_name, 0) + 1
            return self._generate_run_block(device_name, program, kwargs)

        if args and not kwargs and len(args) == 1 and method_name not in {"run", "load_program"}:
            value = args[0]
            self.state[target] = value
            return value

        if not args and not kwargs and target in self.state:
            return self.state[target]

        return {
            "called": True,
            "target": target,
            "args": args,
            "kwargs": kwargs,
        }

    def _generate_run_block(self, device_name, program, kwargs):
        run_index = self.run_counts.get(device_name, 1)
        npoints = int(kwargs.get("npoints") or self._infer_npoints(program) or 256)
        sample_rate = float(self.state.get(f"{device_name}.sample_rate", self.state.get("sequencer.sample_rate", 250_000_000.0)))
        readout_freq = float(self.state.get(f"{device_name}.readout_freq", self.state.get("sequencer.readout_freq", 55_000_000.0)))
        readout_gain = float(self.state.get(f"{device_name}.readout_gain", self.state.get("sequencer.readout_gain", 1.0)))
        control_value = float(self.state.get("dac.voltage", 0.0))
        sensor_value = float(self.state.get("device2.voltage", 0.125))
        current_value = float(self.state.get("multimeter.current", self.state.get("keithley.current", 0.0)))

        seed = self._stable_seed(device_name, program, run_index)
        phase_offset = (seed % 6283) / 1000.0
        detune = 0.02 + ((seed // 10) % 11) * 0.003
        envelope_center = 0.35 + ((seed // 100) % 25) / 100.0
        envelope_width = 0.10 + ((seed // 1000) % 9) / 100.0
        noise_scale = 0.004 + ((seed // 10000) % 7) / 4000.0
        amplitude = readout_gain * (0.65 + 0.07 * math.tanh(control_value - sensor_value * 8.0))
        amplitude *= 1.0 + 0.08 * math.tanh(current_value * 1e6)

        time_axis = []
        i_row = []
        q_row = []
        mag_row = []

        for idx in range(npoints):
            t = idx / sample_rate
            x = idx / max(npoints - 1, 1)
            envelope = math.exp(-((x - envelope_center) ** 2) / max(2.0 * envelope_width ** 2, 1e-9))
            chirp = 2.0 * math.pi * (readout_freq * t + detune * x * x)
            ringdown = math.exp(-2.7 * x)
            base = amplitude * (0.18 + 0.82 * envelope) * ringdown

            i_noise = noise_scale * math.sin((idx + 1) * 0.37 + phase_offset * 0.5)
            q_noise = noise_scale * math.cos((idx + 1) * 0.29 + phase_offset * 0.7)
            i_val = base * math.cos(chirp + phase_offset) + i_noise
            q_val = base * math.sin(chirp + phase_offset) + q_noise
            mag_val = math.sqrt(i_val * i_val + q_val * q_val)

            time_axis.append(round(t * 1e6, 6))
            i_row.append(round(i_val, 6))
            q_row.append(round(q_val, 6))
            mag_row.append(round(mag_val, 6))

        return [time_axis, i_row, q_row, mag_row]

    @staticmethod
    def _infer_npoints(program):
        text = str(program or "")
        for key in ("npoints", "points", "samples", "length"):
            marker = f"{key}="
            if marker in text:
                tail = text.split(marker, 1)[1]
                digits = []
                for ch in tail:
                    if ch.isdigit():
                        digits.append(ch)
                    elif digits:
                        break
                if digits:
                    value = int("".join(digits))
                    if value > 0:
                        return min(value, 4096)
        return None

    @staticmethod
    def _stable_seed(*parts):
        payload = "||".join(str(part) for part in parts).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        return int(digest[:16], 16)

    def _nested_full_state(self):
        return self._nested_from_mapping(self.state)

    @staticmethod
    def _nested_path_value(path, value):
        root = current = {}
        parts = path.split(".")
        for part in parts[:-1]:
            current[part] = {}
            current = current[part]
        current[parts[-1]] = value
        return root

    @classmethod
    def _nested_from_mapping(cls, mapping):
        root = {}
        for path, value in mapping.items():
            current = root
            parts = path.split(".")
            for part in parts[:-1]:
                current = current.setdefault(part, {})
            current[parts[-1]] = value
        return root

    @staticmethod
    def _ok_response(message):
        return {
            "_class_type": "ServerResponse",
            "message": message,
            "error": "None",
        }

    @staticmethod
    def _error_response(error):
        return {
            "_class_type": "ServerResponse",
            "message": None,
            "error": str(error),
        }


def parse_args():
    parser = argparse.ArgumentParser(description="Mock ZMQ instrumentserver for QuAIL demos")
    parser.add_argument("--host", default="*")
    parser.add_argument("--port", type=int, default=5555)
    return parser.parse_args()


def main():
    args = parse_args()
    server = MockInstrumentServer(host=args.host, port=args.port)
    try:
        server.serve_forever()
    finally:
        server.close()


if __name__ == "__main__":
    main()

import json

class InstrumentClient:
    """
    Mock client for instrumentserver.
    In a real implementation, this would use ZMQ or gRPC to talk to the actual server.
    """
    def __init__(self):
        self.connected = True

    def set_parameter(self, instrument, parameter, value):
        print(f"[MOCK] Setting {instrument}.{parameter} to {value}")
        return True

    def get_parameter(self, instrument, parameter):
        print(f"[MOCK] Getting {instrument}.{parameter}")
        return 0.0

    def upload_fpga_code(self, code):
        print(f"[MOCK] Uploading FPGA code: {code[:20]}...")
        return True

    def trigger_fpga(self):
        print(f"[MOCK] Triggering FPGA")
        return True

import time
from expmanager.models import RunLog
from driver_proxy.client import InstrumentClient
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

class ExperimentEngine:
    def __init__(self, run_request):
        self.run_request = run_request
        self.client = InstrumentClient()
        self.channel_layer = get_channel_layer()
        self.group_name = f"run_{run_request.id}"

    def log(self, message, level='INFO'):
        RunLog.objects.create(
            run_request=self.run_request,
            level=level,
            message=message
        )
        # Send to WebSocket
        async_to_sync(self.channel_layer.group_send)(
            self.group_name,
            {
                'type': 'log_message',
                'message': message,
                'level': level
            }
        )

    def run(self):
        script = self.run_request.script
        self.log(f"Starting experiment: {script.name}")
        
        # Sandbox environment
        local_scope = {
            'log': self.log,
            'sleep': time.sleep,
            'instruments': self.client,
            'fpga_code': script.fpga_code
        }
        
        # Execute the script
        exec(script.script_code, {}, local_scope)
        
        self.log("Experiment finished successfully")

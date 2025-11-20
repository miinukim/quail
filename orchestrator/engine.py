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

    def save_data(self, params, data):
        """
        Handle data saving.
        params: tuple of scalar values (axes)
        data: tuple of data vectors (measurements)
        """
        # For now, just log the shape/values
        # In production, this would write to HDF5/Database
        self.log(f"DATA_SAVE: Params={params}, DataShapes={[len(d) for d in data]}")
        
        # Send to WebSocket for real-time plotting (if we had a visualizer)
        async_to_sync(self.channel_layer.group_send)(
            self.group_name,
            {
                'type': 'new_data',
                'params': params,
                'data': [list(d) for d in data] # Convert numpy/lists to serializable
            }
        )

    def run(self):
        script = self.run_request.script
        self.log(f"Starting experiment: {script.name}")
        
        # Context for the parser
        context = {
            'log': self.log,
            'sleep': time.sleep,
            'instruments': self.client,
            'fpga_code': script.fpga_code,
            'save_data': self.save_data
        }
        
        # Execute using custom parser
        from .parser import VirtualProcessor
        parser = VirtualProcessor(context)
        try:
            parser.execute(script.script_code)
        except Exception as e:
            self.log(f"Execution Error: {e}", level='ERROR')
            raise e
        
        self.log("Experiment finished successfully")

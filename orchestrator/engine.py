"""
This file defines the ExperimentEngine class, which executes the experiment script.
It handles logging, data saving, and invoking the VirtualProcessor to run the experiment script.
"""
import time
import os
from expmanager.models import RunLog
from .instrument_client import InstrumentClient
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

import h5py
import numpy as np

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

    def init_data(self, schema):
        """
        Initialize the data file and datasets based on schema.
        schema: {'param_axes': [...], 'num_data': N}
        
        Creates:
        - data: (4, N, num_saves) where N is determined by first save
        - params: (num_params, num_saves) where each column is one parameter set
        """
        # Ensure data directory exists
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_dir = os.path.join(base_dir, 'data')
        os.makedirs(data_dir, exist_ok=True)
        
        # File path: data/run_<id>.h5
        file_path = os.path.join(data_dir, f"run_{self.run_request.id}.h5")
        
        # Update model
        self.run_request.data_file = file_path
        self.run_request.save(update_fields=['data_file'])
        
        num_data = schema['num_data']
        num_params = len(schema['param_axes'])
        
        try:
            with h5py.File(file_path, 'w') as f:
                # Create 3D dataset for data: (num_data, N, num_saves)
                # N will be determined by first save, so we start with a placeholder
                # We'll resize on first save
                f.create_dataset('data', shape=(num_data, 0, 0), maxshape=(num_data, None, None), dtype='f')
                
                # Create 2D dataset for params: (num_params, num_saves)
                f.create_dataset('params', shape=(num_params, 0), maxshape=(num_params, None), dtype='f')
                
                # Save metadata as attributes
                f.attrs['param_axes'] = schema['param_axes']
                f.attrs['num_data'] = num_data
                f.attrs['data_width'] = -1  # Will be set on first save
        except Exception as e:
            self.log(f"Error initializing HDF5: {e}", level='ERROR')
            raise e

    def save_data(self, params, data):
        """
        Handle data saving.
        params: tuple of scalar values (axes) - one per parameter
        data: tuple/list of 4 vectors, each of length N - shape (4, N)
        
        Each save() call adds:
        - One slice to data: data[:, :, i] = new_data (4, N)
        - One column to params: params[:, i] = param_values
        """
        file_path = self.run_request.data_file
        if not file_path or not os.path.exists(file_path):
             self.log("Data file not initialized. Call init_data first.", level='ERROR')
             return

        try:
            with h5py.File(file_path, 'a') as f:
                dset_data = f['data']
                dset_params = f['params']
                
                new_data = np.array(data)  # Shape should be (num_data, N)
                
                if new_data.ndim != 2 or new_data.shape[0] != f.attrs['num_data']:
                    raise ValueError(f"Data must be shape ({f.attrs['num_data']}, N), got {new_data.shape}")
                
                data_width = new_data.shape[1]  # N
                
                # Check/set data width
                if f.attrs['data_width'] == -1:
                    # First save - set the width
                    f.attrs['data_width'] = data_width
                    # Resize data dataset to have proper N dimension
                    dset_data.resize((f.attrs['num_data'], data_width, 0))
                elif f.attrs['data_width'] != data_width:
                    raise ValueError(f"Data width mismatch. Expected N={f.attrs['data_width']}, got N={data_width}")
                
                # Get current number of saves
                num_saves = dset_data.shape[2]
                new_num_saves = num_saves + 1
                
                # Resize to add one more save
                dset_data.resize((f.attrs['num_data'], data_width, new_num_saves))
                dset_params.resize((len(params), new_num_saves))
                
                # Add new data slice
                dset_data[:, :, num_saves] = new_data
                
                # Add new params column
                dset_params[:, num_saves] = np.array(params)

        except Exception as e:
            self.log(f"Error saving HDF5: {e}", level='ERROR')
            raise e

        # Send to WebSocket for real-time plotting
        async_to_sync(self.channel_layer.group_send)(
            self.group_name,
            {
                'type': 'new_data',
                'params': params,
                'data': [list(d) for d in data]
            }
        )

    def check_abort(self):
        """
        Check if the experiment has been aborted by the user.
        """
        self.run_request.refresh_from_db()
        if self.run_request.status == 'CANCELLED':
            raise InterruptedError("Experiment cancelled by user")

    def run(self):
        """
        Entry point for running the experiment synchronously (from Celery).
        """
        script = self.run_request.script
        self.log(f"Starting experiment: {script.name}")
        
        # Determine log directory
        # quail/orchestrator/engine.py -> quail/logs
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        log_dir = os.path.join(base_dir, 'logs')
        
        # Context for the parser
        context = {
            'sleep': time.sleep,
            'instruments': self.client,
            'fpga_code': script.fpga_code,
            'save_data': self.save_data,
            'init_data': self.init_data,
            'check_abort': self.check_abort
        }
        
        # Execute using custom parser
        from .processor import VirtualProcessor
        processor = VirtualProcessor(context, log_dir=log_dir)
        try:
            processor.execute(script.script_code)
        except InterruptedError:
            self.log("Experiment aborted by user", level='WARNING')
            # Status is already CANCELLED in DB, so we don't need to set it again
            # But tasks.py might overwrite it to FAILED if we raise exception.
            # We should probably handle this in tasks.py or re-raise.
            # If we re-raise, tasks.py sets to FAILED.
            # Let's re-raise for now and fix tasks.py if needed.
            raise
        except Exception as e:
            self.log(f"Execution Error: {e}", level='ERROR')
            raise e
        
        self.log("Experiment finished successfully")

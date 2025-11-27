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
    def __init__(self, experiment_run):
        self.experiment_run = experiment_run
        self.client = InstrumentClient()
        self.channel_layer = get_channel_layer()
        self.group_name = f"run_{experiment_run.id}"
        self.h5file = None  # HDF5 file handle, opened by init_data()

    def log(self, message, level='INFO'):
        RunLog.objects.create(
            experiment_run=self.experiment_run,
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
        
        Opens the HDF5 file and keeps it open for the duration of the experiment.
        """
        # Ensure data directory exists
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_dir = os.path.join(base_dir, 'data')
        os.makedirs(data_dir, exist_ok=True)
        
        # File path: data/run_<id>.h5
        file_path = os.path.join(data_dir, f"run_{self.experiment_run.id}.h5")
        
        # Update model
        self.experiment_run.data_file = file_path
        self.experiment_run.save(update_fields=['data_file'])
        
        num_data = schema['num_data']
        num_params = len(schema['param_axes'])
        
        try:
            # Open file and keep it open
            self.h5file = h5py.File(file_path, 'w')
            
            # Create 3D dataset for data: (num_data, N, num_saves)
            # N will be determined by first save, so we start with a placeholder
            # We'll resize on first save
            self.h5file.create_dataset('data', shape=(num_data, 0, 0), maxshape=(num_data, None, None), dtype='f')
            
            # Create 2D dataset for params: (num_params, num_saves)
            self.h5file.create_dataset('params', shape=(num_params, 0), maxshape=(num_params, None), dtype='f')
            
            # Save metadata as attributes
            self.h5file.attrs['param_axes'] = schema['param_axes']
            self.h5file.attrs['num_data'] = num_data
            self.h5file.attrs['data_width'] = -1  # Will be set on first save
            
            self.log(f"Initialized data file: {file_path}")
        except Exception as e:
            self.log(f"Error initializing HDF5: {e}", level='ERROR')
            if self.h5file:
                self.h5file.close()
                self.h5file = None
            raise e

    def save_data(self, params, data):
        """
        Handle data saving.
        params: tuple of scalar values (axes) - one per parameter
        data: tuple/list of 4 vectors, each of length N - shape (4, N)
        
        Each save() call adds:
        - One slice to data: data[:, :, i] = new_data (4, N)
        - One column to params: params[:, i] = param_values
        
        Uses the already-open HDF5 file handle for efficiency.
        """
        if self.h5file is None:
            self.log("Data file not initialized. Call init_data first.", level='ERROR')
            return

        try:
            dset_data = self.h5file['data']
            dset_params = self.h5file['params']
            
            new_data = np.array(data)  # Shape should be (num_data, N)
            
            if new_data.ndim != 2 or new_data.shape[0] != self.h5file.attrs['num_data']:
                raise ValueError(f"Data must be shape ({self.h5file.attrs['num_data']}, N), got {new_data.shape}")
            
            data_width = new_data.shape[1]  # N
            
            # Check/set data width
            if self.h5file.attrs['data_width'] == -1:
                # First save - set the width
                self.h5file.attrs['data_width'] = data_width
                # Resize data dataset to have proper N dimension
                dset_data.resize((self.h5file.attrs['num_data'], data_width, 0))
            elif self.h5file.attrs['data_width'] != data_width:
                raise ValueError(f"Data width mismatch. Expected N={self.h5file.attrs['data_width']}, got N={data_width}")
            
            # Get current number of saves
            num_saves = dset_data.shape[2]
            new_num_saves = num_saves + 1
            
            # Resize to add one more save
            dset_data.resize((self.h5file.attrs['num_data'], data_width, new_num_saves))
            dset_params.resize((len(params), new_num_saves))
            
            # Add new data slice
            dset_data[:, :, num_saves] = new_data
            
            # Add new params column
            dset_params[:, num_saves] = np.array(params)
            
            # Flush to disk periodically for safety (every 10 saves)
            if num_saves % 10 == 0:
                self.h5file.flush()

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

    def close_data_file(self):
        """
        Close the HDF5 file handle if it's open.
        Should be called when the experiment completes or fails.
        """
        if self.h5file:
            try:
                self.h5file.flush()  # Ensure all data is written
                self.h5file.close()
                self.log("Data file closed successfully")
            except Exception as e:
                self.log(f"Error closing HDF5 file: {e}", level='ERROR')
            finally:
                self.h5file = None

    def check_abort(self):
        """
        Check if the experiment has been aborted by the user.
        """
        self.experiment_run.refresh_from_db()
        if self.experiment_run.status == 'CANCELLED':
            raise InterruptedError("Experiment cancelled by user")

    def run(self):
        """
        Entry point for running the experiment synchronously (from Celery).
        """
        script = self.experiment_run.script
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
        
        try:
            # Execute using custom parser
            from .processor import VirtualProcessor
            processor = VirtualProcessor(context, log_dir=log_dir)
            processor.execute(script.script_code)
        except InterruptedError:
            self.log("Experiment aborted by user", level='WARNING')
            raise
        except Exception as e:
            self.log(f"Execution Error: {e}", level='ERROR')
            raise e
        finally:
            # Always close the data file, even on error/abort
            self.close_data_file()
        
        self.log("Experiment finished successfully")

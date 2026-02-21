"""
This file defines the ExperimentEngine class, which executes the experiment script.
It handles logging, data saving, and invoking the VirtualProcessor to run the experiment script.
"""
from django.utils import timezone
import time
import os
import logging
from pathlib import Path
from expmanager.models import RunLog, Dataset
from .instrument_client import InstrumentClient
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

import h5py
import numpy as np

class ExperimentEngine:
    def __init__(self, experiment_run):
        self.experiment_run = experiment_run
        self.logger = logging.getLogger("quail.engine")
        self.client = InstrumentClient()
        # Active health check: fail early if instrumentserver is unreachable.
        self.client.health_check()
        self.logger.info("Instrumentserver health check passed rid=%s", experiment_run.id)
        self.channel_layer = get_channel_layer()
        self.group_name = f"run_{experiment_run.id}"
        self.h5file = None  # current HDF5 file handle
        self.current_dataset = None # current Dataset model instance
        self.logger.info("ExperimentEngine initialized rid=%s", experiment_run.id)

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
        Initialize a new dataset artifact.
        schema: {'param_axes': [...], 'num_data': N, 'name': 'optional_name'}
        """
        # Close previous file if open
        self.close_data_file()

        # Ensure data directory exists
        from django.conf import settings
        data_dir = settings.DATA_DIR
        os.makedirs(data_dir, exist_ok=True)
        
        # Create Dataset Entry
        ds_name = schema.get('name', f"Dataset_{timezone.now().strftime('%H%M%S')}")
        
        # We need to save the Dataset to get an ID for the filename
        self.current_dataset = Dataset.objects.create(
            experiment_run=self.experiment_run,
            name=ds_name,
            file_path="",  # placeholder
            metadata=schema
        )
        
        # Determine Filename
        filename = f"run_{self.experiment_run.id}_ds_{self.current_dataset.id}.h5"
        file_path = os.path.join(data_dir, filename)
        
        # Update Dataset path
        self.current_dataset.file_path = file_path
        self.current_dataset.save()
        
        num_data = schema['num_data']
        num_params = len(schema['param_axes'])
        
        try:
            # Open file 
            self.h5file = h5py.File(file_path, 'w')
            
            # Create 3D dataset for data: (num_data, N, num_saves)
            self.h5file.create_dataset('data', shape=(num_data, 0, 0), maxshape=(num_data, None, None), dtype='f')
            
            # Create 2D dataset for params: (num_params, num_saves)
            self.h5file.create_dataset('params', shape=(num_params, 0), maxshape=(num_params, None), dtype='f')
            
            # Save metadata
            self.h5file.attrs['param_axes'] = schema['param_axes']
            self.h5file.attrs['num_data'] = num_data
            self.h5file.attrs['data_width'] = -1
            
            self.log(f"Initialized dataset '{ds_name}': {filename}")
            
            # Notify frontend of new dataset
            async_to_sync(self.channel_layer.group_send)(
                self.group_name,
                {
                    'type': 'dataset_created',
                    'dataset_id': self.current_dataset.id,
                    'name': ds_name,
                    'file_path': file_path
                }
            )

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
                'dataset_id': self.current_dataset.id,
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
        if self.experiment_run.status in ('CANCELLED', 'ABORTED'):
            raise InterruptedError("Experiment cancelled by user")

    def run(self):
        """
        Entry point for running the experiment synchronously (from Celery).
        """
        script_name, script_code, fpga_code = self._resolve_script_source()
        self.logger.info("ExperimentEngine.run start rid=%s", self.experiment_run.id)
        self.log(f"Starting experiment: {script_name}")
        
        # Determine log directory
        # quail/orchestrator/engine.py -> quail/logs
        # Context for the parser
        context = {
            'sleep': time.sleep,
            'instruments': self.client,
            'fpga_code': fpga_code,
            'save_data': self.save_data,
            'init_data': self.init_data,
            'check_abort': self.check_abort
        }
        
        try:
            # Execute using custom parser
            from .processor import VirtualProcessor
            processor = VirtualProcessor(context)
            processor.execute(script_code)
        except InterruptedError:
            self.log("Experiment aborted by user", level='WARNING')
            self.logger.warning("Experiment aborted rid=%s", self.experiment_run.id)
            raise
        except Exception as e:
            self.log(f"Execution Error: {e}", level='ERROR')
            self.logger.exception("Experiment execution failed rid=%s", self.experiment_run.id)
            raise e
        finally:
            # Always close the data file, even on error/abort
            self.close_data_file()
            self.client.close()
            self.logger.info("ExperimentEngine.run end rid=%s", self.experiment_run.id)
        
        self.log("Experiment finished successfully")

    def _resolve_script_source(self):
        """
        Resolve script name/code/fpga_code for both legacy script-FK runs and
        current script_path-only runs.
        """
        from django.conf import settings

        script_obj = self.experiment_run.script
        script_path = self.experiment_run.script_path

        # Backward-compatible: if a richer script model exists, prefer it.
        if script_obj is not None and hasattr(script_obj, "script_code"):
            name = getattr(script_obj, "name", f"Run {self.experiment_run.id}")
            code = getattr(script_obj, "script_code")
            fpga_code = getattr(script_obj, "fpga_code", None)
            if not code:
                raise ValueError(f"Script object has empty script_code (rid={self.experiment_run.id})")
            return name, code, fpga_code

        if not script_path:
            raise ValueError(f"No script_path set for run {self.experiment_run.id}")

        candidate = Path(script_path)
        paths_to_try = []
        if candidate.is_absolute():
            paths_to_try.append(candidate)
        else:
            paths_to_try.append(Path(settings.USER_SCRIPTS_DIR) / candidate)
            paths_to_try.append(Path(settings.BASE_DIR) / candidate)
            paths_to_try.append(Path(settings.BASE_DIR) / "user_scripts" / candidate)

        resolved = next((p for p in paths_to_try if p.exists()), None)
        if resolved is None:
            raise FileNotFoundError(
                f"Script file not found for run {self.experiment_run.id}: "
                f"script_path={script_path}; tried={[str(p) for p in paths_to_try]}"
            )

        code = resolved.read_text(encoding="utf-8")
        if not code.strip():
            raise ValueError(f"Script file is empty: {resolved}")

        self.logger.info("Resolved script for rid=%s path=%s", self.experiment_run.id, resolved)
        return resolved.name, code, None

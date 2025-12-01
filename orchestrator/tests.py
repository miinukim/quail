from django.test import TestCase
from django.contrib.auth.models import User
from expmanager.models import ExperimentScript, ExperimentRun
from orchestrator.engine import ExperimentEngine
import subprocess
import sys
import os
import time
import h5py
import numpy as np
from unittest.mock import MagicMock, patch

class ExperimentEngineTest(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Start the mock instrument server
        # We assume mock_instrumentserver.py is in the project root (quail/)
        # We need to find its path relative to this file
        # This file is in quail/orchestrator/tests.py
        # Project root is quail/
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        mock_server_path = os.path.join(base_dir, 'mock_instrumentserver.py')
        
        cls.server_process = subprocess.Popen(
            [sys.executable, mock_server_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        time.sleep(2) # Wait for server to start

    @classmethod
    def tearDownClass(cls):
        cls.server_process.terminate()
        cls.server_process.wait()
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password')
        self.script = ExperimentScript.objects.create(
            name="Test Script",
            script_code="""
            # Initialize data with 1 parameter axis 'x'
            # num_data is hardcoded to 4 in VirtualProcessor for now
            init_data(param=("x",))
            
            # Set a parameter on the instrument
            set device.freq 100.0
            
            # Get the parameter back
            param val = get device.freq
            
            # Create some dummy data vectors (length 5)
            d1 = (1, 2, 3, 4, 5)
            d2 = (6, 7, 8, 9, 10)
            d3 = (11, 12, 13, 14, 15)
            d4 = (16, 17, 18, 19, 20)
            
            data_block = (d1, d2, d3, d4)
            
            # Loop 3 times
            for i in (from 0 to 2 step 1) {
                # Save data: params must be a tuple matching param_axes
                save(params=(i,), data=data_block)
            }
            """,
            author=self.user
        )
        self.run_obj = ExperimentRun.objects.create(
            script=self.script,
            user=self.user
        )

    @patch('orchestrator.engine.get_channel_layer')
    @patch('orchestrator.engine.async_to_sync')
    def test_experiment_execution(self, mock_async_to_sync, mock_get_channel_layer):
        # Mock the channel layer to avoid Redis dependency
        mock_channel_layer = MagicMock()
        mock_get_channel_layer.return_value = mock_channel_layer
        
        # Mock async_to_sync to just call the function (or do nothing since we don't care about the return)
        # async_to_sync returns a wrapper that calls the async function. 
        # Since we are mocking the group_send, we can just make async_to_sync return a dummy callable.
        mock_async_to_sync.return_value = lambda *args, **kwargs: None

        engine = ExperimentEngine(self.run_obj)
        engine.run()
        
        # Verify data file was created
        self.run_obj.refresh_from_db()
        self.assertTrue(os.path.exists(self.run_obj.data_file))
        
        # Verify data content
        with h5py.File(self.run_obj.data_file, 'r') as f:
            # Check attributes
            self.assertEqual(f.attrs['num_data'], 4)
            self.assertEqual(f.attrs['data_width'], 5) # Length of d1
            
            # Check params dataset
            # We looped 3 times (0, 1, 2). So shape should be (1, 3)
            params = f['params'][:]
            self.assertEqual(params.shape, (1, 3))
            np.testing.assert_array_equal(params[0], [0, 1, 2])
            
            # Check data dataset
            # Shape should be (4, 5, 3)
            data = f['data'][:]
            self.assertEqual(data.shape, (4, 5, 3))
            
            # Check first slice
            # d1 = (1, 2, 3, 4, 5)
            np.testing.assert_array_equal(data[0, :, 0], [1, 2, 3, 4, 5])
            np.testing.assert_array_equal(data[3, :, 0], [16, 17, 18, 19, 20])
            
            # Check last slice (should be same data since we reused data_block)
            np.testing.assert_array_equal(data[0, :, 2], [1, 2, 3, 4, 5])

        # Clean up data file
        if os.path.exists(self.run_obj.data_file):
            os.remove(self.run_obj.data_file)

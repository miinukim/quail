from django.test import TestCase
from django.contrib.auth.models import User
from django.conf import settings
from expmanager.models import ExperimentScript, ExperimentRun, Dataset
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
        self.script_code = """
            init_data(param=("x",), data=("r0", "r1", "r2", "r3"));
            set device.freq 100.0;
            val = get device.freq;
            d1 = (1, 2, 3, 4, 5);
            d2 = (6, 7, 8, 9, 10);
            d3 = (11, 12, 13, 14, 15);
            d4 = (16, 17, 18, 19, 20);
            data_block = (d1, d2, d3, d4);
            for i in (from 0 to 2 step 1) : {
                save(params=(i,), data=data_block);
            }
        """
        self.script_path = os.path.join(settings.USER_SCRIPTS_DIR, 'orchestrator_test.qil')
        os.makedirs(settings.USER_SCRIPTS_DIR, exist_ok=True)
        with open(self.script_path, 'w', encoding='utf-8') as f:
            f.write(self.script_code)
        self.script = ExperimentScript.objects.create(
            name="Test Script",
            script_path=self.script_path,
            author=self.user
        )
        self.run_obj = ExperimentRun.objects.create(
            script=self.script,
            user=self.user
        )

    def tearDown(self):
        if os.path.exists(self.script_path):
            os.remove(self.script_path)

    @patch('orchestrator.engine.get_channel_layer')
    @patch('orchestrator.engine.async_to_sync')
    @patch('orchestrator.engine.InstrumentClient.health_check', return_value={"ok": True})
    @patch('orchestrator.engine.InstrumentClient.set_parameter', return_value=None)
    @patch('orchestrator.engine.InstrumentClient.get_parameter', return_value=100.0)
    def test_experiment_execution(
        self,
        mock_get_parameter,
        mock_set_parameter,
        mock_health_check,
        mock_async_to_sync,
        mock_get_channel_layer,
    ):
        # Mock the channel layer to avoid Redis dependency
        mock_channel_layer = MagicMock()
        mock_get_channel_layer.return_value = mock_channel_layer
        
        # Mock async_to_sync to just call the function (or do nothing since we don't care about the return)
        # async_to_sync returns a wrapper that calls the async function. 
        # Since we are mocking the group_send, we can just make async_to_sync return a dummy callable.
        mock_async_to_sync.return_value = lambda *args, **kwargs: None

        engine = ExperimentEngine(self.run_obj)
        engine.run()
        
        dataset = Dataset.objects.get(experiment_run=self.run_obj)
        self.assertTrue(os.path.exists(dataset.file_path))
        
        # Verify data content
        with h5py.File(dataset.file_path, 'r') as f:
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
        if os.path.exists(dataset.file_path):
            os.remove(dataset.file_path)

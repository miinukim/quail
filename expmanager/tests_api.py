from django.test import TestCase
from rest_framework.test import APIClient
from django.contrib.auth.models import User
from .models import ExperimentRun, Dataset, ExperimentScript, RunLog
import os
import h5py


class APITest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='tester', password='pass')
        self.client.force_authenticate(user=self.user)

        # ensure user_scripts dir exists
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        scripts_dir = os.path.join(base, 'user_scripts')
        os.makedirs(scripts_dir, exist_ok=True)
        # create a sample script
        self.script_path = os.path.join('example_experiment.qil')
        with open(os.path.join(scripts_dir, self.script_path), 'w') as f:
            f.write('print("hello")')

    def test_files_list_and_read(self):
        resp = self.client.get('/api/files/', {'type': 'script'})
        assert resp.status_code == 200
        assert any(item['name'].startswith('example_experiment') for item in resp.json().get('items', []))

        resp2 = self.client.get('/api/files/read/', {'type': 'script', 'path': self.script_path})
        assert resp2.status_code == 200
        assert 'print("hello")' in resp2.json().get('content', '')

    def test_dataset_meta_and_data_and_logs(self):
        # create script and run
        script = ExperimentScript.objects.create(name='s', script_path='p', author=self.user)
        run = ExperimentRun.objects.create(script=script, user=self.user)

        # create a small h5 data file in data/
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_dir = os.path.join(base, 'data')
        os.makedirs(data_dir, exist_ok=True)
        fname = os.path.join(data_dir, f'run_{run.id}_ds_1.h5')
        with h5py.File(fname, 'w') as f:
            import numpy as np
            f.create_dataset('data', data=np.array([[0.1, 0.2], [0.3, 0.4]]))

        ds = Dataset.objects.create(experiment_run=run, name='counts', file_path=fname, metadata={'units': {'x': 'index'}})
        RunLog.objects.create(experiment_run=run, level='INFO', message='started')

        meta_resp = self.client.get(f'/api/runs/{run.id}/datasets/counts/meta/')
        assert meta_resp.status_code == 200
        assert meta_resp.json().get('name') == 'counts'

        data_resp = self.client.get(f'/api/runs/{run.id}/datasets/counts/data/')
        assert data_resp.status_code == 200
        assert 'data' in data_resp.json()

        logs_resp = self.client.get(f'/api/runs/{run.id}/logs/')
        assert logs_resp.status_code == 200
        assert len(logs_resp.json().get('items', [])) >= 1

    def test_get_data_aggregates_points(self):
        # create script and run
        script = ExperimentScript.objects.create(name='s2', script_path='p2', author=self.user)
        run = ExperimentRun.objects.create(script=script, user=self.user)

        # create a small h5 data file in data/
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_dir = os.path.join(base, 'data')
        os.makedirs(data_dir, exist_ok=True)
        fname = os.path.join(data_dir, f'run_{run.id}_ds_points.h5')
        with h5py.File(fname, 'w') as f:
            import numpy as np
            f.create_dataset('data', data=np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]))

        ds = Dataset.objects.create(experiment_run=run, name='points', file_path=fname, metadata={'params': ['x']})

        resp = self.client.get(f'/api/runs/{run.id}/get_data/')
        assert resp.status_code == 200
        j = resp.json()
        assert 'points' in j
        assert len(j['points']) == 3
        assert j['points'][0]['data'] == [1.0, 2.0]

    def test_create_run_from_panel(self):
        from quail.panels.models import Panel
        p = Panel.objects.create(name='frompanel', script_path='calib/foo.qil', author=self.user)
        resp = self.client.post('/api/runs/', {'panel_id': p.panel_id, 'user': self.user.id})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get('status') == 'QUEUED'

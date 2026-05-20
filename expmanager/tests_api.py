from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from django.contrib.auth.models import User
from types import SimpleNamespace
from unittest.mock import patch
from .models import ExperimentRun, ExperimentSchedule, Dataset, ExperimentScript, RunLog
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

    def test_run_logs_honor_from_seq(self):
        run = ExperimentRun.objects.create(user=self.user, script_path='logs.qil')
        first = RunLog.objects.create(experiment_run=run, level='INFO', message='first')
        RunLog.objects.create(experiment_run=run, level='INFO', message='second')

        resp = self.client.get(f'/api/runs/{run.id}/logs/', {'from_seq': str(first.id)})

        assert resp.status_code == 200
        items = resp.json().get('items', [])
        assert len(items) == 1
        assert items[0]['message'] == 'second'

    def test_dataset_query_projects_and_filters_rows(self):
        script = ExperimentScript.objects.create(name='sq', script_path='p3', author=self.user)
        run = ExperimentRun.objects.create(script=script, user=self.user)

        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_dir = os.path.join(base, 'data')
        os.makedirs(data_dir, exist_ok=True)
        fname = os.path.join(data_dir, f'run_{run.id}_ds_query.h5')
        with h5py.File(fname, 'w') as f:
            import numpy as np
            f.create_dataset('data', data=np.array([[0.1, 0.2], [0.3, 0.4]]))

        Dataset.objects.create(experiment_run=run, name='queryable', file_path=fname, metadata={})
        resp = self.client.post(
            f'/api/runs/{run.id}/datasets/queryable/query/',
            {
                'query': "\n".join([
                    'SELECT row0 AS x, row1 AS y',
                    'WHERE row0 > 0.1',
                    'ORDER BY y DESC',
                    'LIMIT 1',
                ])
            },
            format='json',
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body['columns'] == ['x', 'y']
        assert body['data'] == [[0.2, 0.4]]

    def test_dataset_data_supports_explicit_row_windows(self):
        script = ExperimentScript.objects.create(name='window', script_path='window.qil', author=self.user)
        run = ExperimentRun.objects.create(script=script, user=self.user)

        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_dir = os.path.join(base, 'data')
        os.makedirs(data_dir, exist_ok=True)
        fname = os.path.join(data_dir, f'run_{run.id}_ds_window.h5')
        with h5py.File(fname, 'w') as f:
            import numpy as np
            f.create_dataset('data', data=np.array([[[10.0, 11.0, 12.0], [20.0, 21.0, 22.0]]]))
            f.create_dataset('params', data=np.array([[0.0, 1.0, 2.0]]))
            f.attrs['param_axes'] = ['save']
            f.attrs['data_axes'] = ['value']
            f.attrs['num_data'] = 1

        Dataset.objects.create(experiment_run=run, name='windowed', file_path=fname, metadata={})

        resp = self.client.get(
            f'/api/runs/{run.id}/datasets/windowed/data/',
            {'format': 'json', 'limit_rows': '3', 'offset': '1'},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body['total_rows'] == 6
        assert body['returned_rows'] == 3
        assert body['start_row'] == 1
        assert body['end_row'] == 4
        assert body['truncated'] is True
        assert body['data'] == [[0.0, 20.0], [1.0, 11.0], [1.0, 21.0]]

    def test_dataset_data_defaults_to_tail_window(self):
        script = ExperimentScript.objects.create(name='tail', script_path='tail.qil', author=self.user)
        run = ExperimentRun.objects.create(script=script, user=self.user)

        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_dir = os.path.join(base, 'data')
        os.makedirs(data_dir, exist_ok=True)
        fname = os.path.join(data_dir, f'run_{run.id}_ds_tail.h5')
        with h5py.File(fname, 'w') as f:
            import numpy as np
            f.create_dataset('data', data=np.array([[[10.0, 11.0, 12.0], [20.0, 21.0, 22.0]]]))
            f.create_dataset('params', data=np.array([[0.0, 1.0, 2.0]]))
            f.attrs['param_axes'] = ['save']
            f.attrs['data_axes'] = ['value']
            f.attrs['num_data'] = 1

        Dataset.objects.create(experiment_run=run, name='tail', file_path=fname, metadata={})

        resp = self.client.get(
            f'/api/runs/{run.id}/datasets/tail/data/',
            {'format': 'json', 'limit_rows': '2'},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body['total_rows'] == 6
        assert body['returned_rows'] == 2
        assert body['start_row'] == 4
        assert body['end_row'] == 6
        assert body['data'] == [[2.0, 12.0], [2.0, 22.0]]

    def test_dataset_query_groups_and_aggregates(self):
        script = ExperimentScript.objects.create(name='sg', script_path='p4', author=self.user)
        run = ExperimentRun.objects.create(script=script, user=self.user)

        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_dir = os.path.join(base, 'data')
        os.makedirs(data_dir, exist_ok=True)
        fname = os.path.join(data_dir, f'run_{run.id}_ds_group.h5')
        with h5py.File(fname, 'w') as f:
            import numpy as np
            f.create_dataset('data', data=np.array([[[10.0, 20.0, 30.0]]]))
            f.create_dataset('params', data=np.array([[0.0, 0.0, 1.0]]))
            f.attrs['param_axes'] = ['dummy_row']
            f.attrs['data_axes'] = ['value']

        Dataset.objects.create(experiment_run=run, name='groupable', file_path=fname, metadata={'param_axes': ['dummy_row'], 'data_axes': ['value']})
        resp = self.client.post(
            f'/api/runs/{run.id}/datasets/groupable/query/',
            {
                'query': "\n".join([
                    'GROUP BY dummy_row',
                    'AGG avg(value) AS avg_value, count() AS n',
                    'ORDER BY dummy_row ASC',
                ])
            },
            format='json',
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body['columns'] == ['dummy_row', 'avg_value', 'n']
        assert body['data'] == [[0.0, 15.0, 2], [1.0, 30.0, 1]]

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
        from panels.models import Panel
        p = Panel.objects.create(name='frompanel', script_path='calib/foo.qil', author=self.user)
        with patch('expmanager.tasks.run_experiment_task.delay', return_value=SimpleNamespace(id='run-panel-task')):
            resp = self.client.post('/api/runs/', {'panel_id': p.panel_id, 'user': self.user.id}, format='json')
        assert resp.status_code == 200
        data = resp.json()
        assert data.get('status') == 'QUEUED'
        run = ExperimentRun.objects.get(id=data['rid'])
        assert run.dispatch_task_id == 'run-panel-task'

    def test_create_timed_run_accepts_z_suffix(self):
        with patch('expmanager.tasks.launch_scheduled_run_task.apply_async', return_value=SimpleNamespace(id='timed-launch-task')):
            resp = self.client.post(
                '/api/runs/',
                {
                    'script_path': 'calib/foo.qil',
                    'name': 'timed run',
                    'schedule_type': 'TIMED',
                    'scheduled_at': '2030-01-01T00:00:00Z',
                },
                format='json',
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get('status') == 'SCHEDULED'
        run = ExperimentRun.objects.get(id=data['rid'])
        assert run.dispatch_task_id == 'timed-launch-task'
        assert run.scheduled_at is not None
        assert run.scheduled_at.isoformat().startswith('2030-01-01T00:00:00+00:00')

    def test_create_recurring_weekly_schedule(self):
        with patch('expmanager.tasks.launch_scheduled_run_task.apply_async', return_value=SimpleNamespace(id='recurring-launch-task')):
            resp = self.client.post(
                '/api/runs/',
                {
                    'script_path': 'calib/foo.qil',
                    'name': 'weekly run',
                    'schedule_type': 'RECURRING',
                    'timezone': 'Asia/Seoul',
                    'recurrence': {
                        'kind': 'weekly',
                        'time': '09:30',
                        'weekdays': [0, 2, 4],
                        'every_n_weeks': 1,
                    },
                },
                format='json',
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get('status') == 'SCHEDULED'
        run = ExperimentRun.objects.get(id=data['rid'])
        assert run.schedule is not None
        assert run.dispatch_task_id == 'recurring-launch-task'
        schedule = ExperimentSchedule.objects.get(id=run.schedule_id)
        assert schedule.recurrence['kind'] == 'weekly'
        assert schedule.recurrence['weekdays'] == [0, 2, 4]
        list_resp = self.client.get('/api/runs/')
        assert list_resp.status_code == 200
        items = list_resp.json().get('items', [])
        assert any(item.get('schedule_id') == schedule.schedule_id for item in items)

    def test_start_rejects_running_run(self):
        run = ExperimentRun.objects.create(
            user=self.user,
            script_path='calib/foo.qil',
            status='RUNNING',
        )

        with patch('expmanager.views.run_experiment_task.delay') as delay:
            resp = self.client.post(f'/api/runs/{run.id}/start/')

        assert resp.status_code == 400
        delay.assert_not_called()

    def test_start_requeues_failed_run_and_saves_task_id(self):
        run = ExperimentRun.objects.create(
            user=self.user,
            script_path='calib/foo.qil',
            status='FAILED',
            completed_at=timezone.now(),
        )

        with patch('expmanager.views.run_experiment_task.delay', return_value=SimpleNamespace(id='manual-retry-task')):
            resp = self.client.post(f'/api/runs/{run.id}/start/')

        assert resp.status_code == 200
        run.refresh_from_db()
        assert run.status == 'QUEUED'
        assert run.dispatch_task_id == 'manual-retry-task'
        assert run.started_at is None
        assert run.completed_at is None

    def test_abort_returns_frontend_shape(self):
        run = ExperimentRun.objects.create(
            user=self.user,
            script_path='calib/foo.qil',
            status='RUNNING',
            dispatch_task_id='abort-task',
        )

        with patch('expmanager.scheduling.revoke_task'):
            resp = self.client.post(f'/api/runs/{run.id}/abort/')

        assert resp.status_code == 200
        assert resp.json() == {'rid': run.id, 'status': 'ABORTED'}

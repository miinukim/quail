from django.test import TestCase
from rest_framework.test import APIClient
from django.contrib.auth.models import User
from types import SimpleNamespace
from unittest.mock import patch
from .models import Panel
from expmanager.models import ExperimentRun


class PanelAPITest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='panuser', password='pass')
        self.client.force_authenticate(user=self.user)

    def test_create_and_retrieve_panel(self):
        payload = {'script_path': 'example_experiment.qil', 'name': 'CoolingScan', 'description': 'test', 'tags': ['calib']}
        resp = self.client.post('/api/panels/', payload, format='json')
        assert resp.status_code == 200
        data = resp.json()
        assert 'panel_id' in data

        pid = data['panel_id']
        resp2 = self.client.get(f'/api/panels/{pid}/')
        assert resp2.status_code == 200
        assert resp2.json().get('name') == 'CoolingScan'

    def test_list_panels(self):
        Panel.objects.create(name='p1', script_path='a', author=self.user)
        Panel.objects.create(name='p2', script_path='b', author=self.user)
        resp = self.client.get('/api/panels/listPanels/')
        assert resp.status_code == 200
        assert len(resp.json().get('items', [])) >= 2

    def test_run_now(self):
        p = Panel.objects.create(name='runme', script_path='a', author=self.user)
        with patch('expmanager.tasks.run_experiment_task.delay', return_value=SimpleNamespace(id='run-now-task')):
            resp = self.client.post(
                f'/api/panels/{p.panel_id}/run/',
                {'param_values': {'shots': 10}, 'schedule_type': 'NOW'},
                format='json',
            )
        assert resp.status_code == 200
        assert resp.json().get('status') == 'QUEUED'
        run = ExperimentRun.objects.get(id=resp.json()['rid'])
        assert run.dispatch_task_id == 'run-now-task'

    def test_schedule_timed(self):
        p = Panel.objects.create(name='sched', script_path='b', author=self.user)
        with patch('expmanager.tasks.launch_scheduled_run_task.apply_async', return_value=SimpleNamespace(id='timed-launch-task')):
            resp = self.client.post(
                f'/api/panels/{p.panel_id}/run/',
                {'schedule_type': 'TIMED', 'scheduled_at': '2030-01-01T00:00:00Z'},
                format='json',
            )
        assert resp.status_code == 200
        assert resp.json().get('status') == 'SCHEDULED'
        run = ExperimentRun.objects.get(id=resp.json()['rid'])
        assert run.dispatch_task_id == 'timed-launch-task'

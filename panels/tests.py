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
        assert 'param_schema' in data
        assert 'parameters_schema' not in data
        assert 'fields' not in (data.get('panel') or {})

        pid = data['panel_id']
        resp2 = self.client.get(f'/api/panels/{pid}/')
        assert resp2.status_code == 200
        assert resp2.json().get('name') == 'CoolingScan'
        assert 'fields' not in (resp2.json().get('panel') or {})

    def test_list_panels(self):
        Panel.objects.create(name='p1', script_path='a', author=self.user)
        Panel.objects.create(name='p2', script_path='b', author=self.user)
        direct_resp = self.client.get('/api/panels/')
        assert direct_resp.status_code == 200
        assert isinstance(direct_resp.json().get('items'), list)
        resp = self.client.get('/api/panels/listPanels/')
        assert resp.status_code == 200
        assert len(resp.json().get('items', [])) >= 2

    def test_inspect_classes_returns_frontend_shape(self):
        import os
        from django.conf import settings

        os.makedirs(settings.USER_SCRIPTS_DIR, exist_ok=True)
        script_path = os.path.join(settings.USER_SCRIPTS_DIR, 'classes_contract.qil')
        with open(script_path, 'w', encoding='utf-8') as f:
            f.write('param shots = 10;')

        resp = self.client.post('/api/panels/inspect-classes/', {'script_path': 'classes_contract.qil'}, format='json')

        assert resp.status_code == 200
        assert resp.json() == {'script_path': 'classes_contract.qil', 'classes': []}

        os.remove(script_path)

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

from django.test import TestCase
from rest_framework.test import APIClient
from django.contrib.auth.models import User
from .models import Panel


class PanelAPITest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='panuser', password='pass')
        self.client.force_authenticate(user=self.user)

    def test_create_and_retrieve_panel(self):
        payload = {'script_path': 'example_experiment.qil', 'name': 'CoolingScan', 'description': 'test', 'tags': ['calib']}
        resp = self.client.post('/api/panels/', payload)
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
        resp = self.client.get('/api/panels/list_panels/')
        assert resp.status_code == 200
        assert len(resp.json().get('items', [])) >= 2

    def test_run_now(self):
        p = Panel.objects.create(name='runme', script_path='a', author=self.user)
        resp = self.client.post(f'/api/panels/{p.panel_id}/run/', {'param_values': {'shots': 10}, 'schedule_type': 'NOW'})
        assert resp.status_code == 200
        assert resp.json().get('status') == 'QUEUED'

    def test_schedule_timed(self):
        p = Panel.objects.create(name='sched', script_path='b', author=self.user)
        resp = self.client.post(f'/api/panels/{p.panel_id}/run/', {'schedule_type': 'TIMED', 'scheduled_at': '2030-01-01T00:00:00'})
        assert resp.status_code == 200
        assert resp.json().get('status') == 'SCHEDULED'

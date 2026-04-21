from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from .models import DEFAULT_WORKSPACE_ID, Workspace, WorkspacePreference


class WorkspaceApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="wsuser", password="pass")
        self.client.force_authenticate(user=self.user)

    def test_list_bootstraps_default_workspace(self):
        resp = self.client.get("/api/workspaces/")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["active_workspace_id"], DEFAULT_WORKSPACE_ID)
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["items"][0]["workspace_id"], DEFAULT_WORKSPACE_ID)
        self.assertTrue(Workspace.objects.filter(user=self.user, workspace_id=DEFAULT_WORKSPACE_ID).exists())
        self.assertEqual(WorkspacePreference.objects.get(user=self.user).active_workspace_id, DEFAULT_WORKSPACE_ID)

    def test_create_activate_and_store_layout(self):
        self.client.get("/api/workspaces/")
        create_resp = self.client.post("/api/workspaces/", {"name": "Night Shift"}, format="json")
        self.assertEqual(create_resp.status_code, 201)
        workspace_id = create_resp.json()["workspace_id"]

        activate_resp = self.client.post(f"/api/workspaces/{workspace_id}/activate/", {}, format="json")
        self.assertEqual(activate_resp.status_code, 200)
        self.assertEqual(activate_resp.json()["active_workspace_id"], workspace_id)

        layout = {
            "windows": [{"windowId": "win_1", "workspaceId": workspace_id, "x": 1, "y": 2, "w": 300, "h": 200, "z": 3, "tabs": [], "activeTabId": ""}],
            "minimizedPanels": [],
            "nextZ": 4,
        }
        save_resp = self.client.put(f"/api/workspaces/{workspace_id}/layout/", layout, format="json")
        self.assertEqual(save_resp.status_code, 200)
        get_resp = self.client.get(f"/api/workspaces/{workspace_id}/layout/")
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json()["layout_snapshot"]["nextZ"], 4)

    def test_delete_non_default_workspace(self):
        self.client.get("/api/workspaces/")
        create_resp = self.client.post("/api/workspaces/", {"name": "Temp"}, format="json")
        workspace_id = create_resp.json()["workspace_id"]
        resp = self.client.delete(f"/api/workspaces/{workspace_id}/")
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(Workspace.objects.filter(user=self.user, workspace_id=workspace_id).exists())

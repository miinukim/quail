from django.contrib.auth.models import User
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase


class AuthTokenTests(APITestCase):
    def test_login_regenerates_user_token(self):
        user = User.objects.create_user(username="alice", password="secret")
        old_token = Token.objects.create(user=user)

        resp = self.client.post("/api/login/", {"username": "alice", "password": "secret"}, format="json")

        self.assertEqual(resp.status_code, 200)
        self.assertNotEqual(resp.json()["token"], old_token.key)
        self.assertFalse(Token.objects.filter(key=old_token.key).exists())
        self.assertTrue(Token.objects.filter(user=user, key=resp.json()["token"]).exists())

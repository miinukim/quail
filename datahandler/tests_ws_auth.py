from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser, User
from django.test import TestCase
from rest_framework.authtoken.models import Token

from quail.ws_auth import TokenAuthMiddleware, _extract_token


class TokenAuthMiddlewareTests(TestCase):
    def test_extracts_query_string_token(self):
        scope = {"query_string": b"token=abc123", "headers": []}

        self.assertEqual(_extract_token(scope), "abc123")

    def test_extracts_authorization_token_header(self):
        scope = {"query_string": b"", "headers": [(b"authorization", b"Token abc123")]}

        self.assertEqual(_extract_token(scope), "abc123")

    def test_query_string_token_authenticates_scope_user(self):
        user = User.objects.create_user(username="alice")
        token = Token.objects.create(user=user)
        captured = {}

        async def app(scope, receive, send):
            captured["user"] = scope.get("user")

        scope = {
            "type": "websocket",
            "query_string": f"token={token.key}".encode("utf-8"),
            "headers": [],
            "user": AnonymousUser(),
        }

        async_to_sync(TokenAuthMiddleware(app))(scope, None, None)

        self.assertEqual(captured["user"].id, user.id)
        self.assertTrue(captured["user"].is_authenticated)

    def test_invalid_token_sets_anonymous_user(self):
        captured = {}

        async def app(scope, receive, send):
            captured["user"] = scope.get("user")

        scope = {
            "type": "websocket",
            "query_string": b"token=invalid",
            "headers": [],
            "user": AnonymousUser(),
        }

        async_to_sync(TokenAuthMiddleware(app))(scope, None, None)

        self.assertFalse(captured["user"].is_authenticated)

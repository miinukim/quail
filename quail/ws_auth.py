from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser
from rest_framework.authtoken.models import Token


def _extract_header(headers, name: bytes) -> str | None:
    for key, value in headers:
        if key.lower() == name:
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError:
                return None
    return None


def _extract_token(scope) -> str | None:
    query_string = scope.get("query_string", b"")
    try:
        query = parse_qs(query_string.decode("utf-8"))
    except UnicodeDecodeError:
        query = {}

    values = query.get("token") or []
    if values and values[0]:
        return values[0]

    header = _extract_header(scope.get("headers", []), b"authorization")
    if not header:
        return None

    scheme, _, value = header.partition(" ")
    if scheme.lower() in {"token", "bearer"} and value.strip():
        return value.strip()
    return None


@database_sync_to_async
def _user_for_token(token_key: str):
    try:
        token = Token.objects.select_related("user").get(key=token_key)
    except Token.DoesNotExist:
        return AnonymousUser()
    return token.user if token.user.is_active else AnonymousUser()


class TokenAuthMiddleware:
    """
    Authenticate websocket clients using the same DRF token Maqpie uses for REST.

    AuthMiddlewareStack still handles cookie/session auth first. If a token is
    supplied, it wins over the session user so the browser query-param path and
    non-browser Authorization header path behave consistently.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        scope = dict(scope)
        token_key = _extract_token(scope)
        if token_key:
            scope["user"] = await _user_for_token(token_key)
        return await self.app(scope, receive, send)

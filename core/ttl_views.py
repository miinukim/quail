from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .ttl_state import TTL_DEVICES, TTL_STATE


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ttl_devices(request):
    return Response({"devices": TTL_DEVICES})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ttl_level(request):
    devices = request.data.get("devices") or []
    levels = request.data.get("levels") or []
    if not isinstance(devices, list) or not isinstance(levels, list) or len(devices) != len(levels):
        return Response({"error": "devices and levels must be lists with equal length"}, status=400)
    for dev, lvl in zip(devices, levels):
        if dev in TTL_STATE:
            TTL_STATE[dev]["level"] = str(lvl).upper()
            TTL_STATE[dev]["value"] = TTL_STATE[dev]["level"] == "HIGH"
    return Response({"ok": True})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def ttl_override(request):
    devices = request.data.get("devices") or []
    values = request.data.get("values") or []
    if not isinstance(devices, list) or not isinstance(values, list) or len(devices) != len(values):
        return Response({"error": "devices and values must be lists with equal length"}, status=400)
    for dev, val in zip(devices, values):
        if dev in TTL_STATE:
            TTL_STATE[dev]["override"] = bool(val)
    return Response({"ok": True})

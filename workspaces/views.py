from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import (
    DEFAULT_WORKSPACE_ID,
    DEFAULT_WORKSPACE_NAME,
    Workspace,
    WorkspacePreference,
    default_layout_snapshot,
)
from .serializers import WorkspaceLayoutSerializer, WorkspaceListSerializer, WorkspaceSerializer


def ensure_user_workspaces(user):
    default_workspace, created = Workspace.objects.get_or_create(
        user=user,
        workspace_id=DEFAULT_WORKSPACE_ID,
        defaults={
            "name": DEFAULT_WORKSPACE_NAME,
            "order": 0,
            "is_default": True,
            "layout_snapshot": default_layout_snapshot(),
        },
    )
    if not default_workspace.is_default or default_workspace.order != 0:
        default_workspace.is_default = True
        default_workspace.order = 0
        default_workspace.name = default_workspace.name or DEFAULT_WORKSPACE_NAME
        default_workspace.save(update_fields=["is_default", "order", "name", "updated_at"])
    pref, _ = WorkspacePreference.objects.get_or_create(
        user=user,
        defaults={"active_workspace_id": DEFAULT_WORKSPACE_ID},
    )
    if not Workspace.objects.filter(user=user, workspace_id=pref.active_workspace_id).exists():
        pref.active_workspace_id = DEFAULT_WORKSPACE_ID
        pref.save(update_fields=["active_workspace_id", "updated_at"])
    return pref


class WorkspaceViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = WorkspaceSerializer
    lookup_field = "workspace_id"

    def get_queryset(self):
        ensure_user_workspaces(self.request.user)
        return Workspace.objects.filter(user=self.request.user).order_by("order", "created_at", "id")

    def list(self, request, *args, **kwargs):
        pref = ensure_user_workspaces(request.user)
        items = WorkspaceListSerializer(self.get_queryset(), many=True).data
        return Response({"items": items, "active_workspace_id": pref.active_workspace_id})

    def create(self, request, *args, **kwargs):
        ensure_user_workspaces(request.user)
        data = request.data.copy()
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        order = serializer.validated_data.get("order")
        if order is None:
            existing_orders = list(Workspace.objects.filter(user=request.user).values_list("order", flat=True))
            order = (max(existing_orders) + 1) if existing_orders else 1
        create_kwargs = {
            "user": request.user,
            "name": serializer.validated_data.get("name") or "Workspace",
            "order": order,
            "is_default": False,
            "layout_snapshot": serializer.validated_data.get("layout_snapshot") or default_layout_snapshot(),
        }
        workspace_id = serializer.validated_data.get("workspace_id")
        if workspace_id:
            create_kwargs["workspace_id"] = workspace_id
        instance = Workspace.objects.create(**create_kwargs)
        pref = WorkspacePreference.objects.get(user=request.user)
        pref.active_workspace_id = instance.workspace_id
        pref.save(update_fields=["active_workspace_id", "updated_at"])
        return Response(self.get_serializer(instance).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        workspace = self.get_object()
        data = request.data.copy()
        if workspace.is_default:
            data.pop("is_default", None)
        serializer = self.get_serializer(workspace, data=data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        workspace = self.get_object()
        if workspace.is_default:
            return Response({"error": "Default workspace cannot be deleted"}, status=status.HTTP_400_BAD_REQUEST)
        workspace_id = workspace.workspace_id
        workspace.delete()
        pref = ensure_user_workspaces(request.user)
        if pref.active_workspace_id == workspace_id:
            pref.active_workspace_id = DEFAULT_WORKSPACE_ID
            pref.save(update_fields=["active_workspace_id", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def activate(self, request, workspace_id=None):
        workspace = self.get_object()
        pref = ensure_user_workspaces(request.user)
        pref.active_workspace_id = workspace.workspace_id
        pref.save(update_fields=["active_workspace_id", "updated_at"])
        return Response({"workspace_id": workspace.workspace_id, "active_workspace_id": pref.active_workspace_id})

    @action(detail=True, methods=["get", "put"], url_path="layout")
    def layout(self, request, workspace_id=None):
        workspace = self.get_object()
        if request.method == "GET":
            return Response(
                {
                    "workspace_id": workspace.workspace_id,
                    "layout_snapshot": workspace.layout_snapshot or default_layout_snapshot(),
                }
            )

        serializer = WorkspaceLayoutSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        workspace.layout_snapshot = serializer.validated_data
        workspace.save(update_fields=["layout_snapshot", "updated_at"])
        return Response({"workspace_id": workspace.workspace_id, "layout_snapshot": workspace.layout_snapshot})

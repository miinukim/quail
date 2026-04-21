from rest_framework import serializers

from .models import Workspace


class WorkspaceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Workspace
        fields = [
            "workspace_id",
            "name",
            "order",
            "is_default",
            "layout_snapshot",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at", "is_default"]


class WorkspaceListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Workspace
        fields = ["workspace_id", "name", "order", "is_default", "updated_at"]
        read_only_fields = fields


class WorkspaceLayoutSerializer(serializers.Serializer):
    windows = serializers.ListField(child=serializers.JSONField(), required=False, default=list)
    minimizedPanels = serializers.ListField(child=serializers.JSONField(), required=False, default=list)
    nextZ = serializers.IntegerField(required=False, default=2, min_value=1)

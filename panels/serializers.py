from rest_framework import serializers
from .models import Panel, PanelConfig


class PanelSerializer(serializers.ModelSerializer):
    class Meta:
        model = Panel
        fields = '__all__'


class PanelCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Panel
        fields = ('script_path', 'name', 'class_name', 'description', 'tags')


class PanelConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = PanelConfig
        fields = '__all__'

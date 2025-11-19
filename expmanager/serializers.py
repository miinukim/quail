from rest_framework import serializers
from .models import ExperimentScript, RunRequest, RunLog

class ExperimentScriptSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExperimentScript
        fields = '__all__'

class RunRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = RunRequest
        fields = '__all__'

class RunLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = RunLog
        fields = '__all__'

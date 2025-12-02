from rest_framework import serializers
from .models import ExperimentScript, ExperimentRun, RunLog

class ExperimentScriptSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExperimentScript
        fields = '__all__'

class ExperimentRunSerializer(serializers.ModelSerializer):
    script_name = serializers.CharField(source='script.name', read_only=True)

    class Meta:
        model = ExperimentRun
        fields = '__all__'

class RunLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = RunLog
        fields = '__all__'

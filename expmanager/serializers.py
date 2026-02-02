"""
DRF serializers for experiment scripts, runs, datasets, and logs.
"""
from rest_framework import serializers
from .models import ExperimentScript, ExperimentRun, RunLog, Dataset, Archive

class ExperimentScriptSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExperimentScript
        fields = '__all__'

class DatasetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Dataset
        fields = '__all__'

class ExperimentRunSerializer(serializers.ModelSerializer):
    script_name = serializers.SerializerMethodField()
    datasets = DatasetSerializer(many=True, read_only=True)

    class Meta:
        model = ExperimentRun
        fields = '__all__'

    def get_script_name(self, obj):
        if obj.script:
            return obj.script.name
        if obj.script_path:
            return obj.script_path.split("/")[-1]
        return "Ad-hoc Run"


class ExperimentRunListSerializer(serializers.ModelSerializer):
    script_name = serializers.SerializerMethodField()

    class Meta:
        model = ExperimentRun
        fields = '__all__'

    def get_script_name(self, obj):
        if obj.script:
            return obj.script.name
        if obj.script_path:
            return obj.script_path.split("/")[-1]
        return "Ad-hoc Run"

class RunLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = RunLog
        fields = '__all__'


class ArchiveSerializer(serializers.ModelSerializer):
    class Meta:
        model = Archive
        fields = '__all__'

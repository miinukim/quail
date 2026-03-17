"""
DRF serializers for experiment scripts, runs, datasets, and logs.
"""
from rest_framework import serializers
from .models import ExperimentScript, ExperimentRun, RunLog, Dataset, Archive
from .scheduling import recurrence_summary

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
    schedule_id = serializers.SerializerMethodField()
    schedule_status = serializers.SerializerMethodField()
    recurrence = serializers.SerializerMethodField()
    recurrence_summary = serializers.SerializerMethodField()

    class Meta:
        model = ExperimentRun
        fields = '__all__'

    def get_script_name(self, obj):
        if obj.script:
            return obj.script.name
        if obj.script_path:
            return obj.script_path.split("/")[-1]
        return "Ad-hoc Run"

    def get_schedule_id(self, obj):
        return obj.schedule.schedule_id if obj.schedule_id and obj.schedule else None

    def get_schedule_status(self, obj):
        return obj.schedule.status if obj.schedule_id and obj.schedule else None

    def get_recurrence(self, obj):
        return obj.schedule.recurrence if obj.schedule_id and obj.schedule else None

    def get_recurrence_summary(self, obj):
        if obj.schedule_id and obj.schedule:
            return recurrence_summary(obj.schedule.recurrence)
        return None


class ExperimentRunListSerializer(serializers.ModelSerializer):
    script_name = serializers.SerializerMethodField()
    schedule_id = serializers.SerializerMethodField()
    schedule_status = serializers.SerializerMethodField()
    recurrence = serializers.SerializerMethodField()
    recurrence_summary = serializers.SerializerMethodField()

    class Meta:
        model = ExperimentRun
        fields = '__all__'

    def get_script_name(self, obj):
        if obj.script:
            return obj.script.name
        if obj.script_path:
            return obj.script_path.split("/")[-1]
        return "Ad-hoc Run"

    def get_schedule_id(self, obj):
        return obj.schedule.schedule_id if obj.schedule_id and obj.schedule else None

    def get_schedule_status(self, obj):
        return obj.schedule.status if obj.schedule_id and obj.schedule else None

    def get_recurrence(self, obj):
        return obj.schedule.recurrence if obj.schedule_id and obj.schedule else None

    def get_recurrence_summary(self, obj):
        if obj.schedule_id and obj.schedule:
            return recurrence_summary(obj.schedule.recurrence)
        return None

class RunLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = RunLog
        fields = '__all__'


class ArchiveSerializer(serializers.ModelSerializer):
    class Meta:
        model = Archive
        fields = '__all__'

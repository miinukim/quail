from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/experiment/(?P<run_id>\d+)/$', consumers.ExperimentConsumer.as_asgi()),
]

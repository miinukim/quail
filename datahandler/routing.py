from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'runs/(?P<run_id>\d+)/status/$', consumers.RunStatusConsumer.as_asgi()),
    re_path(r'runs/(?P<run_id>\d+)/logs/$', consumers.RunLogConsumer.as_asgi()),
    re_path(r'runs/(?P<run_id>\d+)/datasets/(?P<dataset_id>[^/]+)/stream/$', consumers.DatasetStreamConsumer.as_asgi()),
    re_path(r'ttl/status/$', consumers.TTLStatusConsumer.as_asgi()),
]

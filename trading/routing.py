"""
WebSocket URL routing for the trading app.
"""
from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/dashboard/$', consumers.DashboardConsumer.as_asgi()),
    re_path(r'ws/prices/$', consumers.PriceStreamConsumer.as_asgi()),
]

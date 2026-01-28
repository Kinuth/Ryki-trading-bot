"""
URL configuration for ryki_trading project.
"""
from django.contrib import admin
from django.urls import path, include

from trading.views import DashboardView

urlpatterns = [
    path('', DashboardView.as_view(), name='dashboard'),
    path('admin/', admin.site.urls),
    path('api/', include('trading.urls')),
]


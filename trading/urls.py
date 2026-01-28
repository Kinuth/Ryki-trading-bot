"""
URL configuration for the trading API.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    TradeViewSet, PositionViewSet, RiskStateViewSet, EconomicEventViewSet,
    SystemControlView, PauseSystemView, ResumeSystemView,
    PricesView, AccountView, ManualTradeView
)

router = DefaultRouter()
router.register(r'trades', TradeViewSet, basename='trade')
router.register(r'positions', PositionViewSet, basename='position')
router.register(r'risk', RiskStateViewSet, basename='risk')
router.register(r'events', EconomicEventViewSet, basename='event')

urlpatterns = [
    path('', include(router.urls)),
    
    # System control endpoints
    path('system/status/', SystemControlView.as_view(), name='system-status'),
    path('system/pause/', PauseSystemView.as_view(), name='system-pause'),
    path('system/resume/', ResumeSystemView.as_view(), name='system-resume'),
    
    # Market data endpoints
    path('prices/', PricesView.as_view(), name='prices'),
    path('account/', AccountView.as_view(), name='account'),
    
    # Trading endpoints
    path('trade/', ManualTradeView.as_view(), name='manual-trade'),
]

"""
REST API views for the trading system.
"""
import logging
from decimal import Decimal
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.conf import settings

from django.shortcuts import render
from django.views import View

from .models import Trade, Position, RiskState, EconomicEvent, MarketData
from .serializers import (
    TradeSerializer, PositionSerializer, RiskStateSerializer,
    EconomicEventSerializer, MarketDataSerializer,
    PauseSystemSerializer, ManualTradeSerializer, ClosePositionSerializer
)

logger = logging.getLogger('trading')


class DashboardView(View):
    """
    Main trading dashboard view.
    Renders the dashboard HTML template.
    """
    
    def get(self, request):
        """Render the trading dashboard."""
        from django.conf import settings
        
        context = {
            'trading_pairs': settings.TRADING_PAIRS,
            'testnet': settings.BINANCE_TESTNET,
            'testnet_text': 'Testnet' if settings.BINANCE_TESTNET else 'Mainnet',
        }
        return render(request, 'trading/dashboard.html', context)


class TradeViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing trades.
    
    Endpoints:
    - GET /api/trades/ - List all trades
    - GET /api/trades/{id}/ - Get trade details
    """
    queryset = Trade.objects.all()
    serializer_class = TradeSerializer
    permission_classes = [AllowAny]  # Change to IsAuthenticated in production
    
    def get_queryset(self):
        queryset = Trade.objects.all()
        
        # Filter by symbol
        symbol = self.request.query_params.get('symbol')
        if symbol:
            queryset = queryset.filter(symbol=symbol)
        
        # Filter by status
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        # Filter by side
        side = self.request.query_params.get('side')
        if side:
            queryset = queryset.filter(side=side)
        
        return queryset.order_by('-created_at')


class PositionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing and managing positions.
    
    Endpoints:
    - GET /api/positions/ - List all positions
    - GET /api/positions/{id}/ - Get position details
    - POST /api/positions/{id}/close/ - Close a position
    """
    queryset = Position.objects.all()
    serializer_class = PositionSerializer
    permission_classes = [AllowAny]
    
    def get_queryset(self):
        queryset = Position.objects.all()
        
        # Filter by status
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        else:
            # Default to open positions
            queryset = queryset.filter(status=Position.Status.OPEN)
        
        # Filter by symbol
        symbol = self.request.query_params.get('symbol')
        if symbol:
            queryset = queryset.filter(symbol=symbol)
        
        return queryset.order_by('-opened_at')
    
    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        """Close a specific position."""
        position = self.get_object()
        
        if position.status != Position.Status.OPEN:
            return Response(
                {'error': 'Position is already closed'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        serializer = ClosePositionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        from .tasks import close_position
        result = close_position.delay(position.id, serializer.validated_data['reason'])
        
        return Response({
            'message': 'Position close initiated',
            'task_id': result.id
        })


class RiskStateViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing risk state.
    
    Endpoints:
    - GET /api/risk/ - List risk states
    - GET /api/risk/today/ - Get today's risk state
    """
    queryset = RiskState.objects.all()
    serializer_class = RiskStateSerializer
    permission_classes = [AllowAny]
    
    @action(detail=False, methods=['get'])
    def today(self, request):
        """Get today's risk state."""
        from django.utils import timezone
        
        risk_state = RiskState.get_or_create_today()
        serializer = self.get_serializer(risk_state)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def metrics(self, request):
        """Get current risk metrics."""
        from .services.risk_manager import RiskManager
        from .services.binance_client import BinanceClient
        from .services.redis_cache import RedisCache
        
        try:
            rm = RiskManager(
                binance_client=BinanceClient(),
                redis_cache=RedisCache()
            )
            metrics = rm.get_current_risk_metrics()
            return Response(metrics)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class EconomicEventViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing economic events.
    
    Endpoints:
    - GET /api/events/ - List economic events
    - GET /api/events/upcoming/ - Get upcoming events
    """
    queryset = EconomicEvent.objects.all()
    serializer_class = EconomicEventSerializer
    permission_classes = [AllowAny]
    
    @action(detail=False, methods=['get'])
    def upcoming(self, request):
        """Get upcoming economic events."""
        from django.utils import timezone
        from datetime import timedelta
        
        now = timezone.now()
        queryset = EconomicEvent.objects.filter(
            release_time__gt=now,
            release_time__lt=now + timedelta(hours=24)
        ).order_by('release_time')
        
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


class SystemControlView(APIView):
    """
    API for controlling the trading system.
    
    Endpoints:
    - GET /api/system/status/ - Get system status
    - POST /api/system/pause/ - Pause trading
    - POST /api/system/resume/ - Resume trading
    """
    permission_classes = [AllowAny]
    
    def get(self, request):
        """Get current system status."""
        from .services.redis_cache import RedisCache
        
        try:
            cache = RedisCache()
            status_data = cache.get_system_status()
            
            risk_state = RiskState.get_or_create_today()
            
            return Response({
                **status_data,
                'db_status': risk_state.system_status,
                'daily_pnl': str(risk_state.daily_pnl),
                'drawdown_pct': str(risk_state.drawdown_pct),
                'trading_pairs': settings.TRADING_PAIRS,
            })
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class PauseSystemView(APIView):
    """Pause the trading system."""
    permission_classes = [AllowAny]
    
    def post(self, request):
        """Pause trading."""
        serializer = PauseSystemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        from .services.risk_manager import RiskManager
        from .services.binance_client import BinanceClient
        from .services.redis_cache import RedisCache
        
        try:
            rm = RiskManager(
                binance_client=BinanceClient(),
                redis_cache=RedisCache()
            )
            rm.trigger_circuit_breaker(serializer.validated_data['reason'])
            
            return Response({'message': 'System paused', 'reason': serializer.validated_data['reason']})
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ResumeSystemView(APIView):
    """Resume the trading system."""
    permission_classes = [AllowAny]
    
    def post(self, request):
        """Resume trading."""
        from .services.redis_cache import RedisCache
        
        try:
            cache = RedisCache()
            cache.set_system_status('ACTIVE', '')
            
            risk_state = RiskState.get_or_create_today()
            risk_state.system_status = RiskState.SystemStatus.ACTIVE
            risk_state.pause_reason = ''
            risk_state.save()
            
            return Response({'message': 'System resumed'})
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class PricesView(APIView):
    """Get current prices for all trading pairs."""
    permission_classes = [AllowAny]
    
    def get(self, request):
        """Get current prices."""
        from .services.redis_cache import RedisCache
        from .services.binance_client import BinanceClient
        
        try:
            cache = RedisCache()
            client = BinanceClient()
            
            prices = {}
            for symbol in settings.TRADING_PAIRS:
                price = cache.get_price(symbol)
                if price is None:
                    try:
                        price = client.get_ticker_price(symbol)
                        cache.set_price(symbol, price)
                    except:
                        price = None
                prices[symbol] = str(price) if price else None
            
            return Response(prices)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class AccountView(APIView):
    """Get Binance account information."""
    permission_classes = [AllowAny]
    
    def get(self, request):
        """Get account balance."""
        from .services.binance_client import BinanceClient
        
        try:
            client = BinanceClient()
            balance = client.get_account_balance('USDT')
            
            return Response({
                'usdt_balance': str(balance),
                'testnet': settings.BINANCE_TESTNET,
            })
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ManualTradeView(APIView):
    """Place a manual trade."""
    permission_classes = [AllowAny]
    
    def post(self, request):
        """Place a manual trade."""
        serializer = ManualTradeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        from .tasks import execute_trade
        
        data = serializer.validated_data
        signal_dict = {
            'symbol': data['symbol'],
            'action': data['side'],
            'quantity': str(data['quantity']),
            'entry_price': str(data.get('price', '0')),
            'stop_loss': str(data.get('stop_loss', '0')),
            'take_profit': str(data.get('take_profit')) if data.get('take_profit') else None,
            'vpa_pattern': 'MANUAL',
            'three_d_confluence': 'N/A',
            'ema_deviation': '0',
            'macro_context': 'Manual trade',
        }
        
        result = execute_trade.delay(signal_dict)
        
        return Response({
            'message': 'Trade initiated',
            'task_id': result.id
        })

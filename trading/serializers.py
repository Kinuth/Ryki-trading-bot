"""
DRF Serializers for the trading API.
"""
from decimal import Decimal
from rest_framework import serializers
from .models import Trade, Position, RiskState, EconomicEvent, MarketData


class TradeSerializer(serializers.ModelSerializer):
    """Serializer for Trade model."""
    
    class Meta:
        model = Trade
        fields = [
            'id', 'binance_order_id', 'symbol', 'side', 'order_type',
            'requested_quantity', 'filled_quantity', 'requested_price',
            'execution_price', 'average_price', 'slippage', 'slippage_pct',
            'pnl', 'pnl_pct', 'commission', 'macro_context', 'vpa_signal',
            'three_d_signal', 'ema_deviation', 'status', 'created_at',
            'updated_at', 'filled_at'
        ]
        read_only_fields = fields


class PositionSerializer(serializers.ModelSerializer):
    """Serializer for Position model."""
    
    entry_trade_id = serializers.IntegerField(source='entry_trade.id', read_only=True)
    exit_trade_id = serializers.IntegerField(source='exit_trade.id', read_only=True, allow_null=True)
    
    class Meta:
        model = Position
        fields = [
            'id', 'entry_trade_id', 'exit_trade_id', 'symbol', 'side',
            'quantity', 'entry_price', 'current_price', 'unrealized_pnl',
            'unrealized_pnl_pct', 'initial_stop', 'current_stop',
            'trailing_activated', 'trailing_distance', 'highest_price',
            'lowest_price', 'take_profit', 'status', 'close_reason',
            'opened_at', 'closed_at'
        ]
        read_only_fields = fields


class RiskStateSerializer(serializers.ModelSerializer):
    """Serializer for RiskState model."""
    
    win_rate = serializers.SerializerMethodField()
    
    class Meta:
        model = RiskState
        fields = [
            'id', 'date', 'starting_balance', 'current_balance',
            'highest_balance', 'daily_pnl', 'daily_pnl_pct', 'drawdown',
            'drawdown_pct', 'max_drawdown_pct', 'total_trades',
            'winning_trades', 'losing_trades', 'win_rate', 'system_status',
            'pause_reason', 'paused_at', 'created_at', 'updated_at'
        ]
        read_only_fields = fields
    
    def get_win_rate(self, obj):
        if obj.total_trades > 0:
            return round(obj.winning_trades / obj.total_trades * 100, 2)
        return 0


class EconomicEventSerializer(serializers.ModelSerializer):
    """Serializer for EconomicEvent model."""
    
    class Meta:
        model = EconomicEvent
        fields = [
            'id', 'event_type', 'country', 'title', 'release_time',
            'forecast', 'actual', 'previous', 'impact',
            'deviation_from_forecast', 'source', 'created_at'
        ]
        read_only_fields = fields


class MarketDataSerializer(serializers.ModelSerializer):
    """Serializer for MarketData model."""
    
    class Meta:
        model = MarketData
        fields = [
            'id', 'symbol', 'timeframe', 'open_time', 'open_price',
            'high_price', 'low_price', 'close_price', 'volume',
            'close_time', 'quote_volume', 'trade_count', 'spread',
            'body', 'upper_wick', 'lower_wick', 'close_position'
        ]
        read_only_fields = fields


# =========================================================================
# INPUT SERIALIZERS
# =========================================================================

class PauseSystemSerializer(serializers.Serializer):
    """Serializer for pausing the trading system."""
    reason = serializers.CharField(max_length=200, required=False, default='Manual pause')


class ManualTradeSerializer(serializers.Serializer):
    """Serializer for placing manual trades."""
    symbol = serializers.CharField(max_length=20)
    side = serializers.ChoiceField(choices=['BUY', 'SELL'])
    quantity = serializers.DecimalField(max_digits=18, decimal_places=8)
    price = serializers.DecimalField(max_digits=18, decimal_places=8, required=False)
    order_type = serializers.ChoiceField(choices=['MARKET', 'LIMIT'], default='LIMIT')
    stop_loss = serializers.DecimalField(max_digits=18, decimal_places=8, required=False)
    take_profit = serializers.DecimalField(max_digits=18, decimal_places=8, required=False)


class ClosePositionSerializer(serializers.Serializer):
    """Serializer for closing a position."""
    reason = serializers.CharField(max_length=50, required=False, default='MANUAL')

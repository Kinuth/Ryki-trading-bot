"""
Django admin configuration for the trading app.
"""
from django.contrib import admin
from .models import Trade, Position, RiskState, EconomicEvent, MarketData


@admin.register(Trade)
class TradeAdmin(admin.ModelAdmin):
    """Admin for Trade model."""
    
    list_display = [
        'binance_order_id', 'symbol', 'side', 'status',
        'filled_quantity', 'average_price', 'pnl', 'created_at'
    ]
    list_filter = ['symbol', 'side', 'status', 'created_at']
    search_fields = ['binance_order_id', 'symbol', 'macro_context']
    readonly_fields = [
        'binance_order_id', 'binance_client_order_id', 'slippage',
        'slippage_pct', 'pnl', 'pnl_pct', 'created_at', 'updated_at'
    ]
    date_hierarchy = 'created_at'
    ordering = ['-created_at']


@admin.register(Position)
class PositionAdmin(admin.ModelAdmin):
    """Admin for Position model."""
    
    list_display = [
        'symbol', 'side', 'status', 'quantity', 'entry_price',
        'current_price', 'unrealized_pnl_pct', 'trailing_activated', 'opened_at'
    ]
    list_filter = ['symbol', 'side', 'status', 'trailing_activated']
    search_fields = ['symbol']
    readonly_fields = [
        'entry_trade', 'exit_trade', 'unrealized_pnl', 'unrealized_pnl_pct',
        'opened_at', 'closed_at'
    ]
    date_hierarchy = 'opened_at'
    ordering = ['-opened_at']
    
    fieldsets = (
        ('Position Info', {
            'fields': ('symbol', 'side', 'quantity', 'entry_price', 'status')
        }),
        ('Stop Loss', {
            'fields': ('initial_stop', 'current_stop', 'trailing_activated', 'trailing_distance')
        }),
        ('Profit Tracking', {
            'fields': ('take_profit', 'current_price', 'unrealized_pnl', 'unrealized_pnl_pct')
        }),
        ('Linked Trades', {
            'fields': ('entry_trade', 'exit_trade', 'close_reason')
        }),
        ('Timestamps', {
            'fields': ('opened_at', 'closed_at')
        }),
    )


@admin.register(RiskState)
class RiskStateAdmin(admin.ModelAdmin):
    """Admin for RiskState model."""
    
    list_display = [
        'date', 'system_status', 'current_balance', 'daily_pnl_pct',
        'drawdown_pct', 'total_trades', 'winning_trades'
    ]
    list_filter = ['system_status', 'date']
    readonly_fields = [
        'daily_pnl', 'daily_pnl_pct', 'drawdown', 'drawdown_pct',
        'max_drawdown_pct', 'created_at', 'updated_at'
    ]
    date_hierarchy = 'date'
    ordering = ['-date']
    
    fieldsets = (
        ('Status', {
            'fields': ('date', 'system_status', 'pause_reason', 'paused_at')
        }),
        ('Balance', {
            'fields': ('starting_balance', 'current_balance', 'highest_balance')
        }),
        ('Daily Performance', {
            'fields': ('daily_pnl', 'daily_pnl_pct', 'drawdown', 'drawdown_pct', 'max_drawdown_pct')
        }),
        ('Trade Stats', {
            'fields': ('total_trades', 'winning_trades', 'losing_trades')
        }),
    )


@admin.register(EconomicEvent)
class EconomicEventAdmin(admin.ModelAdmin):
    """Admin for EconomicEvent model."""
    
    list_display = [
        'event_type', 'country', 'release_time', 'impact',
        'forecast', 'actual', 'deviation_from_forecast'
    ]
    list_filter = ['event_type', 'country', 'impact', 'source']
    search_fields = ['title']
    date_hierarchy = 'release_time'
    ordering = ['-release_time']


@admin.register(MarketData)
class MarketDataAdmin(admin.ModelAdmin):
    """Admin for MarketData model."""
    
    list_display = [
        'symbol', 'timeframe', 'open_time', 'open_price',
        'high_price', 'low_price', 'close_price', 'volume'
    ]
    list_filter = ['symbol', 'timeframe']
    date_hierarchy = 'open_time'
    ordering = ['-open_time']

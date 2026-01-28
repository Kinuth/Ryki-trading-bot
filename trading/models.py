"""
Database models for Ryki Trading System.
Tracks trades, positions, risk state, and market data.
"""
from decimal import Decimal
from django.db import models
from django.utils import timezone


class Trade(models.Model):
    """
    Records every executed trade with full context.
    Includes VPA signals, macro context, and execution details.
    """
    
    class Side(models.TextChoices):
        BUY = 'BUY', 'Buy'
        SELL = 'SELL', 'Sell'
    
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        PARTIALLY_FILLED = 'PARTIALLY_FILLED', 'Partially Filled'
        FILLED = 'FILLED', 'Filled'
        CANCELLED = 'CANCELLED', 'Cancelled'
        REJECTED = 'REJECTED', 'Rejected'
    
    # Binance order info
    binance_order_id = models.CharField(max_length=64, unique=True, db_index=True)
    binance_client_order_id = models.CharField(max_length=64, blank=True)
    
    # Trade details
    symbol = models.CharField(max_length=20, db_index=True)  # e.g., BTCUSDT
    side = models.CharField(max_length=4, choices=Side.choices)
    order_type = models.CharField(max_length=20, default='LIMIT')  # LIMIT, MARKET
    
    # Quantities
    requested_quantity = models.DecimalField(max_digits=18, decimal_places=8)
    filled_quantity = models.DecimalField(max_digits=18, decimal_places=8, default=Decimal('0'))
    
    # Prices
    requested_price = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    execution_price = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    average_price = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    
    # Slippage analysis
    expected_price = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    slippage = models.DecimalField(max_digits=18, decimal_places=8, default=Decimal('0'))
    slippage_pct = models.DecimalField(max_digits=8, decimal_places=6, default=Decimal('0'))
    
    # PnL tracking
    pnl = models.DecimalField(max_digits=18, decimal_places=8, default=Decimal('0'))
    pnl_pct = models.DecimalField(max_digits=8, decimal_places=6, default=Decimal('0'))
    commission = models.DecimalField(max_digits=18, decimal_places=8, default=Decimal('0'))
    
    # Strategy context
    macro_context = models.CharField(max_length=200, blank=True)  # e.g., "Post-CPI Volatility"
    vpa_signal = models.CharField(max_length=50, blank=True)  # e.g., "CLIMAX_BAR", "NO_DEMAND"
    three_d_signal = models.CharField(max_length=50, blank=True)  # e.g., "BULLISH_CONFLUENCE"
    ema_deviation = models.DecimalField(max_digits=8, decimal_places=6, null=True, blank=True)
    
    # Status
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    filled_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['symbol', 'status']),
            models.Index(fields=['created_at']),
        ]
    
    def __str__(self):
        return f"{self.side} {self.filled_quantity}/{self.requested_quantity} {self.symbol} @ {self.execution_price or 'pending'}"
    
    @property
    def is_complete(self):
        return self.status in [self.Status.FILLED, self.Status.CANCELLED, self.Status.REJECTED]
    
    def calculate_slippage(self):
        """Calculate slippage from expected to actual execution price."""
        if self.expected_price and self.average_price:
            self.slippage = self.average_price - self.expected_price
            self.slippage_pct = (self.slippage / self.expected_price) * 100
            self.save(update_fields=['slippage', 'slippage_pct'])


class Position(models.Model):
    """
    Active positions with stop-loss tracking and trailing stop logic.
    """
    
    class Status(models.TextChoices):
        OPEN = 'OPEN', 'Open'
        CLOSED = 'CLOSED', 'Closed'
    
    # Link to entry trade
    entry_trade = models.ForeignKey(Trade, on_delete=models.PROTECT, related_name='positions')
    exit_trade = models.ForeignKey(Trade, on_delete=models.PROTECT, related_name='closed_positions', 
                                    null=True, blank=True)
    
    # Position details
    symbol = models.CharField(max_length=20, db_index=True)
    side = models.CharField(max_length=4, choices=Trade.Side.choices)
    quantity = models.DecimalField(max_digits=18, decimal_places=8)
    entry_price = models.DecimalField(max_digits=18, decimal_places=8)
    
    # Current state
    current_price = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    unrealized_pnl = models.DecimalField(max_digits=18, decimal_places=8, default=Decimal('0'))
    unrealized_pnl_pct = models.DecimalField(max_digits=8, decimal_places=6, default=Decimal('0'))
    
    # Stop loss management
    initial_stop = models.DecimalField(max_digits=18, decimal_places=8)
    current_stop = models.DecimalField(max_digits=18, decimal_places=8)
    trailing_activated = models.BooleanField(default=False)
    trailing_distance = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    highest_price = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)  # For trailing
    lowest_price = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)   # For shorts
    
    # Take profit (optional)
    take_profit = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    
    # Status
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    close_reason = models.CharField(max_length=50, blank=True)  # e.g., "STOP_LOSS", "TRAILING_STOP", "TAKE_PROFIT"
    
    # Timestamps
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-opened_at']
        indexes = [
            models.Index(fields=['symbol', 'status']),
        ]
    
    def __str__(self):
        return f"{self.side} {self.quantity} {self.symbol} @ {self.entry_price} ({self.status})"
    
    def update_unrealized_pnl(self, current_price: Decimal):
        """Update unrealized PnL based on current price."""
        self.current_price = current_price
        if self.side == Trade.Side.BUY:
            self.unrealized_pnl = (current_price - self.entry_price) * self.quantity
        else:
            self.unrealized_pnl = (self.entry_price - current_price) * self.quantity
        
        self.unrealized_pnl_pct = (self.unrealized_pnl / (self.entry_price * self.quantity)) * 100
        self.save(update_fields=['current_price', 'unrealized_pnl', 'unrealized_pnl_pct'])
    
    def update_trailing_stop(self, current_price: Decimal, trailing_trigger_pct: Decimal):
        """Update trailing stop if conditions are met."""
        profit_pct = self.unrealized_pnl_pct
        
        # Activate trailing stop at trigger percentage
        if not self.trailing_activated and profit_pct >= trailing_trigger_pct * 100:
            self.trailing_activated = True
            self.trailing_distance = abs(current_price - self.current_stop)
            self.highest_price = current_price if self.side == Trade.Side.BUY else None
            self.lowest_price = current_price if self.side == Trade.Side.SELL else None
        
        # Update trailing stop
        if self.trailing_activated:
            if self.side == Trade.Side.BUY:
                if current_price > (self.highest_price or Decimal('0')):
                    self.highest_price = current_price
                    new_stop = current_price - self.trailing_distance
                    if new_stop > self.current_stop:
                        self.current_stop = new_stop
            else:  # SELL (short)
                if self.lowest_price is None or current_price < self.lowest_price:
                    self.lowest_price = current_price
                    new_stop = current_price + self.trailing_distance
                    if new_stop < self.current_stop:
                        self.current_stop = new_stop
        
        self.save()


class RiskState(models.Model):
    """
    Daily risk tracking for circuit breaker functionality.
    One record per day to track drawdown and system status.
    """
    
    class SystemStatus(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        PAUSED = 'PAUSED', 'Paused'
        EMERGENCY_STOP = 'EMERGENCY_STOP', 'Emergency Stop'
    
    date = models.DateField(unique=True, db_index=True)
    
    # Balance tracking
    starting_balance = models.DecimalField(max_digits=18, decimal_places=8)
    current_balance = models.DecimalField(max_digits=18, decimal_places=8)
    highest_balance = models.DecimalField(max_digits=18, decimal_places=8)
    
    # PnL
    daily_pnl = models.DecimalField(max_digits=18, decimal_places=8, default=Decimal('0'))
    daily_pnl_pct = models.DecimalField(max_digits=8, decimal_places=6, default=Decimal('0'))
    
    # Drawdown
    drawdown = models.DecimalField(max_digits=18, decimal_places=8, default=Decimal('0'))
    drawdown_pct = models.DecimalField(max_digits=8, decimal_places=6, default=Decimal('0'))
    max_drawdown_pct = models.DecimalField(max_digits=8, decimal_places=6, default=Decimal('0'))
    
    # Trade counts
    total_trades = models.IntegerField(default=0)
    winning_trades = models.IntegerField(default=0)
    losing_trades = models.IntegerField(default=0)
    
    # System status
    system_status = models.CharField(max_length=20, choices=SystemStatus.choices, default=SystemStatus.ACTIVE)
    pause_reason = models.CharField(max_length=200, blank=True)
    paused_at = models.DateTimeField(null=True, blank=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-date']
        verbose_name = 'Risk State'
        verbose_name_plural = 'Risk States'
    
    def __str__(self):
        return f"RiskState {self.date}: {self.system_status} (DD: {self.drawdown_pct}%)"
    
    def update_balance(self, new_balance: Decimal):
        """Update current balance and recalculate metrics."""
        self.current_balance = new_balance
        
        # Update highest balance
        if new_balance > self.highest_balance:
            self.highest_balance = new_balance
        
        # Calculate daily PnL
        self.daily_pnl = new_balance - self.starting_balance
        if self.starting_balance > 0:
            self.daily_pnl_pct = (self.daily_pnl / self.starting_balance) * 100
        
        # Calculate drawdown from highest
        self.drawdown = self.highest_balance - new_balance
        if self.highest_balance > 0:
            self.drawdown_pct = (self.drawdown / self.highest_balance) * 100
        
        # Track max drawdown
        if self.drawdown_pct > self.max_drawdown_pct:
            self.max_drawdown_pct = self.drawdown_pct
        
        self.save()
    
    def trigger_circuit_breaker(self, reason: str = "Daily drawdown limit exceeded"):
        """Pause trading due to circuit breaker trigger."""
        self.system_status = self.SystemStatus.PAUSED
        self.pause_reason = reason
        self.paused_at = timezone.now()
        self.save()
    
    @classmethod
    def get_or_create_today(cls, starting_balance: Decimal = None):
        """Get or create today's risk state."""
        today = timezone.now().date()
        risk_state, created = cls.objects.get_or_create(
            date=today,
            defaults={
                'starting_balance': starting_balance or Decimal('0'),
                'current_balance': starting_balance or Decimal('0'),
                'highest_balance': starting_balance or Decimal('0'),
            }
        )
        return risk_state


class EconomicEvent(models.Model):
    """
    Stores CPI/PPI and other macro economic events for strategy timing.
    """
    
    class EventType(models.TextChoices):
        CPI = 'CPI', 'Consumer Price Index'
        PPI = 'PPI', 'Producer Price Index'
        NFP = 'NFP', 'Non-Farm Payrolls'
        FOMC = 'FOMC', 'Federal Reserve Meeting'
        GDP = 'GDP', 'Gross Domestic Product'
        OTHER = 'OTHER', 'Other'
    
    class Impact(models.TextChoices):
        LOW = 'LOW', 'Low'
        MEDIUM = 'MEDIUM', 'Medium'
        HIGH = 'HIGH', 'High'
    
    event_type = models.CharField(max_length=10, choices=EventType.choices)
    country = models.CharField(max_length=10, default='US')  # ISO country code
    title = models.CharField(max_length=200)
    
    # Timing
    release_time = models.DateTimeField(db_index=True)
    
    # Values
    forecast = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    actual = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    previous = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    
    # Impact assessment
    impact = models.CharField(max_length=10, choices=Impact.choices, default=Impact.MEDIUM)
    deviation_from_forecast = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    
    # Data source tracking
    source = models.CharField(max_length=50)  # 'investing.com' or 'tradingeconomics.com'
    external_id = models.CharField(max_length=100, blank=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-release_time']
        unique_together = ['event_type', 'country', 'release_time']
    
    def __str__(self):
        return f"{self.event_type} {self.country} @ {self.release_time}"
    
    def calculate_deviation(self):
        """Calculate deviation from forecast after actual is released."""
        if self.actual is not None and self.forecast is not None and self.forecast != 0:
            self.deviation_from_forecast = ((self.actual - self.forecast) / abs(self.forecast)) * 100
            self.save(update_fields=['deviation_from_forecast'])


class MarketData(models.Model):
    """
    Stores candlestick/kline data for analysis.
    Used for VPA pattern recognition and technical analysis.
    """
    
    symbol = models.CharField(max_length=20, db_index=True)
    timeframe = models.CharField(max_length=10)  # 1m, 5m, 15m, 1h, 4h, 1d
    
    # OHLCV data
    open_time = models.DateTimeField(db_index=True)
    open_price = models.DecimalField(max_digits=18, decimal_places=8)
    high_price = models.DecimalField(max_digits=18, decimal_places=8)
    low_price = models.DecimalField(max_digits=18, decimal_places=8)
    close_price = models.DecimalField(max_digits=18, decimal_places=8)
    volume = models.DecimalField(max_digits=24, decimal_places=8)
    close_time = models.DateTimeField()
    
    # Quote asset volume
    quote_volume = models.DecimalField(max_digits=24, decimal_places=8, null=True, blank=True)
    
    # Trade counts
    trade_count = models.IntegerField(null=True, blank=True)
    
    # Calculated fields (for VPA)
    spread = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)  # high - low
    body = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)    # |open - close|
    upper_wick = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    lower_wick = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    close_position = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)  # 0-1 position in range
    
    class Meta:
        unique_together = ['symbol', 'timeframe', 'open_time']
        ordering = ['-open_time']
        indexes = [
            models.Index(fields=['symbol', 'timeframe', 'open_time']),
        ]
    
    def __str__(self):
        return f"{self.symbol} {self.timeframe} @ {self.open_time}"
    
    def save(self, *args, **kwargs):
        """Calculate derived fields before saving."""
        self.spread = self.high_price - self.low_price
        self.body = abs(self.open_price - self.close_price)
        
        if self.close_price >= self.open_price:  # Bullish candle
            self.upper_wick = self.high_price - self.close_price
            self.lower_wick = self.open_price - self.low_price
        else:  # Bearish candle
            self.upper_wick = self.high_price - self.open_price
            self.lower_wick = self.close_price - self.low_price
        
        # Close position in range (0 = bottom, 1 = top)
        if self.spread > 0:
            self.close_position = (self.close_price - self.low_price) / self.spread
        else:
            self.close_position = Decimal('0.5')
        
        super().save(*args, **kwargs)

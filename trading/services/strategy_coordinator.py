"""
Strategy Coordinator Service.
Orchestrates VPA and 3D analysis to generate trading signals.
"""
import logging
from decimal import Decimal
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from enum import Enum
from django.conf import settings
from django.utils import timezone

from .vpa_analyzer import VPAAnalyzer, VPASignal, VPAPattern, TrendDirection
from .three_d_analyzer import ThreeDAnalyzer, ThreeDSignal, DimensionAlignment
from .risk_manager import RiskManager
from .redis_cache import RedisCache
from .binance_client import BinanceClient
from trading.models import Trade, Position

logger = logging.getLogger('trading')


class SignalAction(Enum):
    """Trading signal action."""
    BUY = 'BUY'
    SELL = 'SELL'
    HOLD = 'HOLD'
    CLOSE_LONG = 'CLOSE_LONG'
    CLOSE_SHORT = 'CLOSE_SHORT'


@dataclass
class TradeSignal:
    """Complete trading signal with all context."""
    symbol: str
    action: SignalAction
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Optional[Decimal]
    quantity: Decimal
    confidence: float  # 0.0 to 1.0
    
    # Strategy context
    vpa_pattern: str
    vpa_description: str
    three_d_confluence: str
    ema_deviation: Decimal
    macro_context: str
    
    # Validation
    is_valid: bool
    rejection_reason: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'symbol': self.symbol,
            'action': self.action.value,
            'entry_price': str(self.entry_price),
            'stop_loss': str(self.stop_loss),
            'take_profit': str(self.take_profit) if self.take_profit else None,
            'quantity': str(self.quantity),
            'confidence': self.confidence,
            'vpa_pattern': self.vpa_pattern,
            'vpa_description': self.vpa_description,
            'three_d_confluence': self.three_d_confluence,
            'ema_deviation': str(self.ema_deviation),
            'macro_context': self.macro_context,
            'is_valid': self.is_valid,
            'rejection_reason': self.rejection_reason,
        }


class StrategyCoordinator:
    """
    Orchestrates the trading strategy by combining VPA and 3D analysis.
    
    Signal Generation Logic:
    1. VPA pattern must be present and valid
    2. 3D dimensions must align (≥2/3)
    3. Price deviation from 20-EMA must exceed threshold
    4. Only signal after macro announcements (within window)
    5. Risk manager must approve position size and slippage
    """
    
    def __init__(self):
        """Initialize strategy coordinator with all required services."""
        self.binance_client = BinanceClient()
        self.redis_cache = RedisCache()
        self.vpa_analyzer = VPAAnalyzer(lookback_period=settings.EMA_PERIOD)
        self.three_d_analyzer = ThreeDAnalyzer(
            redis_cache=self.redis_cache,
            binance_client=self.binance_client
        )
        self.risk_manager = RiskManager(
            binance_client=self.binance_client,
            redis_cache=self.redis_cache
        )
        
        self.ema_period = settings.EMA_PERIOD
        self.ema_deviation_threshold = Decimal(str(settings.EMA_DEVIATION_THRESHOLD))
        self.timeframes = ['1m', '5m', '15m', '1h']
    
    def evaluate_symbol(self, symbol: str) -> Optional[TradeSignal]:
        """
        Evaluate a single symbol for trading signals.
        
        Args:
            symbol: Trading pair symbol (e.g., 'BTCUSDT')
            
        Returns:
            TradeSignal if conditions are met, None otherwise
        """
        try:
            logger.debug(f"Evaluating {symbol}")
            
            # Check if trading is allowed
            is_allowed, reason = self.risk_manager.is_trading_allowed()
            if not is_allowed:
                logger.info(f"Trading not allowed: {reason}")
                return None
            
            # Check for existing position
            existing_position = Position.objects.filter(
                symbol=symbol,
                status=Position.Status.OPEN
            ).first()
            
            if existing_position:
                # Check if we should close the position
                return self._evaluate_exit(symbol, existing_position)
            
            # Get market data
            klines_by_tf = self._fetch_klines(symbol)
            
            if not klines_by_tf or '1m' not in klines_by_tf:
                logger.warning(f"No kline data for {symbol}")
                return None
            
            # Get current price
            current_price = self._get_current_price(symbol)
            if not current_price:
                return None
            
            # Get related prices for correlation analysis
            related_prices = self._get_related_prices()
            
            # Run VPA analysis on primary timeframe
            vpa_signal = self.vpa_analyzer.analyze(klines_by_tf['1m'])
            
            # Run 3D analysis
            three_d_signal = self.three_d_analyzer.analyze(
                symbol=symbol,
                klines_by_timeframe=klines_by_tf,
                related_prices=related_prices
            )
            
            # Calculate EMA deviation
            ema_deviation = self._calculate_ema_deviation(klines_by_tf['1m'])
            
            # Generate signal if conditions are met
            signal = self._generate_signal(
                symbol=symbol,
                current_price=current_price,
                vpa_signal=vpa_signal,
                three_d_signal=three_d_signal,
                ema_deviation=ema_deviation
            )
            
            if signal and signal.is_valid:
                # Cache the signal
                self.redis_cache.set_signal(symbol, signal.to_dict())
                logger.info(f"Valid signal generated: {signal.action.value} {symbol}")
            
            return signal
            
        except Exception as e:
            logger.error(f"Error evaluating {symbol}: {e}", exc_info=True)
            return None
    
    def evaluate_all_symbols(self) -> List[TradeSignal]:
        """
        Evaluate all configured trading pairs for signals.
        
        Returns:
            List of valid trade signals
        """
        signals = []
        
        for symbol in settings.TRADING_PAIRS:
            signal = self.evaluate_symbol(symbol)
            if signal and signal.is_valid:
                signals.append(signal)
        
        return signals
    
    def _fetch_klines(self, symbol: str) -> Dict[str, List[Dict[str, Any]]]:
        """Fetch klines for all timeframes."""
        klines_by_tf = {}
        
        for tf in self.timeframes:
            try:
                # Try cache first
                cached = self.redis_cache.get_kline_history(symbol, tf, count=50)
                
                if len(cached) >= 20:
                    klines_by_tf[tf] = cached
                else:
                    # Fetch from Binance
                    klines = self.binance_client.get_klines(symbol, tf, limit=50)
                    klines_by_tf[tf] = klines
                    
                    # Cache the latest
                    if klines:
                        self.redis_cache.set_latest_kline(symbol, tf, klines[-1])
                        
            except Exception as e:
                logger.warning(f"Error fetching {tf} klines for {symbol}: {e}")
        
        return klines_by_tf
    
    def _get_current_price(self, symbol: str) -> Optional[Decimal]:
        """Get current price from cache or API."""
        try:
            # Try cache first
            price = self.redis_cache.get_price(symbol)
            
            if price is None:
                price = self.binance_client.get_ticker_price(symbol)
                self.redis_cache.set_price(symbol, price)
            
            return price
            
        except Exception as e:
            logger.error(f"Error getting price for {symbol}: {e}")
            return None
    
    def _get_related_prices(self) -> Dict[str, Decimal]:
        """Get prices for related assets for correlation analysis."""
        related_symbols = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT']
        prices = {}
        
        for symbol in related_symbols:
            price = self._get_current_price(symbol)
            if price:
                prices[symbol] = price
        
        return prices
    
    def _calculate_ema_deviation(self, klines: List[Dict[str, Any]]) -> Decimal:
        """Calculate current price deviation from EMA."""
        if len(klines) < self.ema_period:
            return Decimal('0')
        
        closes = [float(k['close']) for k in klines]
        current_price = closes[-1]
        
        # Calculate EMA
        multiplier = 2 / (self.ema_period + 1)
        import numpy as np
        ema = np.mean(closes[:self.ema_period])
        
        for price in closes[self.ema_period:]:
            ema = (price - ema) * multiplier + ema
        
        # Calculate deviation
        deviation = (current_price - ema) / ema if ema > 0 else 0
        
        return Decimal(str(deviation))
    
    def _generate_signal(
        self,
        symbol: str,
        current_price: Decimal,
        vpa_signal: VPASignal,
        three_d_signal: ThreeDSignal,
        ema_deviation: Decimal
    ) -> TradeSignal:
        """
        Generate trading signal from combined analysis.
        
        Criteria:
        1. VPA must show valid pattern
        2. 3D must show confluence
        3. EMA deviation must exceed threshold
        4. Direction must align
        """
        # Default rejection
        rejection_reason = ""
        is_valid = False
        action = SignalAction.HOLD
        
        # Check VPA validity
        if not vpa_signal.is_valid_signal:
            rejection_reason = f"VPA not valid: {vpa_signal.pattern.value}"
        
        # Check 3D validity
        elif not three_d_signal.is_valid_signal:
            rejection_reason = f"3D not valid: {three_d_signal.confluence.value}"
        
        # Check EMA deviation
        elif abs(ema_deviation) < self.ema_deviation_threshold:
            rejection_reason = f"EMA deviation {ema_deviation:.4f} below threshold"
        
        # Check direction alignment
        else:
            vpa_direction = vpa_signal.direction
            td_direction = three_d_signal.confluence
            
            # Map to common direction
            bullish_vpa = vpa_direction == TrendDirection.BULLISH
            bullish_td = td_direction == DimensionAlignment.BULLISH
            
            bearish_vpa = vpa_direction == TrendDirection.BEARISH
            bearish_td = td_direction == DimensionAlignment.BEARISH
            
            if bullish_vpa and bullish_td and ema_deviation < 0:
                # Bullish signal - price below EMA (good entry)
                action = SignalAction.BUY
                is_valid = True
            elif bearish_vpa and bearish_td and ema_deviation > 0:
                # Bearish signal - price above EMA (good entry)
                action = SignalAction.SELL
                is_valid = True
            else:
                rejection_reason = "VPA/3D direction mismatch or EMA not in favor"
        
        # Calculate stop loss
        atr = self._calculate_atr(symbol)
        stop_loss = self.risk_manager.get_stop_loss_price(
            entry_price=current_price,
            side=action.value if action in [SignalAction.BUY, SignalAction.SELL] else 'BUY',
            atr=atr
        )
        
        # Calculate position size (only if valid)
        quantity = Decimal('0')
        if is_valid:
            position_result = self.risk_manager.calculate_position_size(
                symbol=symbol,
                entry_price=current_price,
                stop_price=stop_loss
            )
            
            if not position_result.is_valid:
                is_valid = False
                rejection_reason = f"Position sizing failed: {position_result.reason}"
            else:
                quantity = position_result.quantity
                
                # Check slippage
                slippage_check = self.risk_manager.check_slippage(
                    symbol=symbol,
                    side=action.value,
                    quantity=quantity
                )
                
                if not slippage_check.is_acceptable:
                    is_valid = False
                    rejection_reason = f"Slippage too high: {slippage_check.reason}"
        
        # Calculate take profit (2:1 risk/reward)
        take_profit = None
        if is_valid and stop_loss:
            risk_distance = abs(current_price - stop_loss)
            if action == SignalAction.BUY:
                take_profit = current_price + (risk_distance * 2)
            else:
                take_profit = current_price - (risk_distance * 2)
        
        # Calculate confidence score
        confidence = self._calculate_confidence(vpa_signal, three_d_signal)
        
        # Build macro context description
        macro_context = self._build_macro_context(three_d_signal)
        
        return TradeSignal(
            symbol=symbol,
            action=action,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            quantity=quantity,
            confidence=confidence,
            vpa_pattern=vpa_signal.pattern.value,
            vpa_description=vpa_signal.description,
            three_d_confluence=three_d_signal.confluence.value,
            ema_deviation=ema_deviation,
            macro_context=macro_context,
            is_valid=is_valid,
            rejection_reason=rejection_reason
        )
    
    def _evaluate_exit(
        self,
        symbol: str,
        position: Position
    ) -> Optional[TradeSignal]:
        """
        Evaluate if an existing position should be closed.
        
        Check for:
        1. Stop loss hit
        2. Take profit hit
        3. VPA reversal signal
        """
        current_price = self._get_current_price(symbol)
        if not current_price:
            return None
        
        # Update position with current price
        position.update_unrealized_pnl(current_price)
        
        # Check stop loss
        is_long = position.side == Trade.Side.BUY
        
        if is_long:
            if current_price <= position.current_stop:
                return self._create_exit_signal(
                    symbol, position, current_price,
                    SignalAction.CLOSE_LONG,
                    "Stop loss triggered"
                )
            if position.take_profit and current_price >= position.take_profit:
                return self._create_exit_signal(
                    symbol, position, current_price,
                    SignalAction.CLOSE_LONG,
                    "Take profit reached"
                )
        else:
            if current_price >= position.current_stop:
                return self._create_exit_signal(
                    symbol, position, current_price,
                    SignalAction.CLOSE_SHORT,
                    "Stop loss triggered"
                )
            if position.take_profit and current_price <= position.take_profit:
                return self._create_exit_signal(
                    symbol, position, current_price,
                    SignalAction.CLOSE_SHORT,
                    "Take profit reached"
                )
        
        return None
    
    def _create_exit_signal(
        self,
        symbol: str,
        position: Position,
        current_price: Decimal,
        action: SignalAction,
        reason: str
    ) -> TradeSignal:
        """Create exit signal for closing a position."""
        return TradeSignal(
            symbol=symbol,
            action=action,
            entry_price=current_price,
            stop_loss=Decimal('0'),
            take_profit=None,
            quantity=position.quantity,
            confidence=1.0,
            vpa_pattern='EXIT',
            vpa_description=reason,
            three_d_confluence='N/A',
            ema_deviation=Decimal('0'),
            macro_context=reason,
            is_valid=True,
            rejection_reason=""
        )
    
    def _calculate_atr(self, symbol: str, period: int = 14) -> Optional[Decimal]:
        """Calculate Average True Range for stop loss calculation."""
        try:
            klines = self.binance_client.get_klines(symbol, '1h', limit=period + 1)
            
            if len(klines) < 2:
                return None
            
            true_ranges = []
            for i in range(1, len(klines)):
                high = float(klines[i]['high'])
                low = float(klines[i]['low'])
                prev_close = float(klines[i-1]['close'])
                
                tr = max(
                    high - low,
                    abs(high - prev_close),
                    abs(low - prev_close)
                )
                true_ranges.append(tr)
            
            if true_ranges:
                import numpy as np
                atr = np.mean(true_ranges)
                return Decimal(str(atr))
            
            return None
            
        except Exception as e:
            logger.warning(f"Error calculating ATR for {symbol}: {e}")
            return None
    
    def _calculate_confidence(
        self,
        vpa_signal: VPASignal,
        three_d_signal: ThreeDSignal
    ) -> float:
        """Calculate overall signal confidence (0.0 to 1.0)."""
        vpa_weight = 0.4
        td_weight = 0.6
        
        confidence = (
            vpa_signal.strength * vpa_weight +
            three_d_signal.confluence_score * td_weight
        )
        
        return min(max(confidence, 0.0), 1.0)
    
    def _build_macro_context(self, three_d_signal: ThreeDSignal) -> str:
        """Build macro context description for trade logging."""
        parts = []
        
        if three_d_signal.fundamental.post_event_window:
            recent = three_d_signal.fundamental.recent_events
            if recent:
                event_type = recent[0].get('event_type', 'MACRO')
                parts.append(f"Post-{event_type} Volatility")
        
        parts.append(f"3D: {three_d_signal.confluence.value}")
        parts.append(f"Crypto: {three_d_signal.relational.crypto_health.value}")
        
        return " | ".join(parts) if parts else "Normal Market Conditions"

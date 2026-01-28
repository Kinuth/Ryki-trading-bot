"""
Risk Manager Service.
Handles position sizing, slippage protection, trailing stops, and circuit breaker.
"""
import logging
from decimal import Decimal
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass
from django.conf import settings
from django.utils import timezone

from trading.models import Position, RiskState, Trade

logger = logging.getLogger('trading')


@dataclass
class PositionSizeResult:
    """Result of position sizing calculation."""
    quantity: Decimal
    risk_amount: Decimal
    stop_distance: Decimal
    position_value: Decimal
    risk_pct: Decimal
    is_valid: bool
    reason: str


@dataclass
class SlippageCheck:
    """Result of slippage analysis."""
    estimated_slippage_pct: Decimal
    is_acceptable: bool
    sufficient_liquidity: bool
    estimated_avg_price: Decimal
    reason: str


class RiskManager:
    """
    Comprehensive risk management for the trading system.
    
    Responsibilities:
    1. Position sizing based on account risk
    2. Slippage protection using order book analysis
    3. Trailing stop-loss management
    4. Circuit breaker for daily drawdown limit
    """
    
    def __init__(self, binance_client=None, redis_cache=None):
        """
        Initialize risk manager.
        
        Args:
            binance_client: BinanceClient instance for market data and orders
            redis_cache: RedisCache instance for cached data
        """
        self.binance_client = binance_client
        self.redis_cache = redis_cache
        
        # Load settings
        self.account_risk_pct = Decimal(str(settings.ACCOUNT_RISK_PCT))
        self.max_slippage_pct = Decimal(str(settings.MAX_SLIPPAGE_PCT))
        self.trailing_trigger_pct = Decimal(str(settings.TRAILING_TRIGGER_PCT))
        self.daily_drawdown_limit = Decimal(str(settings.DAILY_DRAWDOWN_LIMIT))
    
    # =========================================================================
    # POSITION SIZING
    # =========================================================================
    
    def calculate_position_size(
        self,
        symbol: str,
        entry_price: Decimal,
        stop_price: Decimal,
        account_balance: Optional[Decimal] = None
    ) -> PositionSizeResult:
        """
        Calculate position size based on account risk percentage.
        
        Formula:
            risk_amount = account_balance * risk_pct
            risk_per_unit = |entry_price - stop_price|
            quantity = risk_amount / risk_per_unit
        
        Args:
            symbol: Trading pair symbol
            entry_price: Intended entry price
            stop_price: Stop-loss price
            account_balance: Account balance (fetched if not provided)
            
        Returns:
            PositionSizeResult with calculated quantity and validation
        """
        try:
            # Get account balance if not provided
            if account_balance is None:
                if not self.binance_client:
                    return PositionSizeResult(
                        quantity=Decimal('0'),
                        risk_amount=Decimal('0'),
                        stop_distance=Decimal('0'),
                        position_value=Decimal('0'),
                        risk_pct=self.account_risk_pct,
                        is_valid=False,
                        reason="No Binance client available"
                    )
                account_balance = self.binance_client.get_account_balance('USDT')
            
            if account_balance <= 0:
                return PositionSizeResult(
                    quantity=Decimal('0'),
                    risk_amount=Decimal('0'),
                    stop_distance=Decimal('0'),
                    position_value=Decimal('0'),
                    risk_pct=self.account_risk_pct,
                    is_valid=False,
                    reason="Insufficient account balance"
                )
            
            # Calculate risk amount (1.5% of account)
            risk_amount = account_balance * self.account_risk_pct
            
            # Calculate stop distance
            stop_distance = abs(entry_price - stop_price)
            
            if stop_distance <= 0:
                return PositionSizeResult(
                    quantity=Decimal('0'),
                    risk_amount=risk_amount,
                    stop_distance=Decimal('0'),
                    position_value=Decimal('0'),
                    risk_pct=self.account_risk_pct,
                    is_valid=False,
                    reason="Invalid stop distance (must be > 0)"
                )
            
            # Calculate quantity
            quantity = risk_amount / stop_distance
            
            # Format quantity to symbol precision
            if self.binance_client:
                quantity = self.binance_client.format_quantity(symbol, quantity)
            
            # Calculate position value
            position_value = quantity * entry_price
            
            # Validate minimum notional
            if self.binance_client:
                symbol_info = self.binance_client.get_symbol_info(symbol)
                min_notional = symbol_info.get('min_notional', Decimal('10'))
                min_qty = symbol_info.get('min_qty', Decimal('0'))
                
                if position_value < min_notional:
                    return PositionSizeResult(
                        quantity=quantity,
                        risk_amount=risk_amount,
                        stop_distance=stop_distance,
                        position_value=position_value,
                        risk_pct=self.account_risk_pct,
                        is_valid=False,
                        reason=f"Position value {position_value} below minimum notional {min_notional}"
                    )
                
                if quantity < min_qty:
                    return PositionSizeResult(
                        quantity=quantity,
                        risk_amount=risk_amount,
                        stop_distance=stop_distance,
                        position_value=position_value,
                        risk_pct=self.account_risk_pct,
                        is_valid=False,
                        reason=f"Quantity {quantity} below minimum {min_qty}"
                    )
            
            logger.info(
                f"Position size calculated: {quantity} {symbol} "
                f"(risk: ${risk_amount:.2f}, stop: {stop_distance})"
            )
            
            return PositionSizeResult(
                quantity=quantity,
                risk_amount=risk_amount,
                stop_distance=stop_distance,
                position_value=position_value,
                risk_pct=self.account_risk_pct,
                is_valid=True,
                reason="Position size valid"
            )
            
        except Exception as e:
            logger.error(f"Error calculating position size: {e}")
            return PositionSizeResult(
                quantity=Decimal('0'),
                risk_amount=Decimal('0'),
                stop_distance=Decimal('0'),
                position_value=Decimal('0'),
                risk_pct=self.account_risk_pct,
                is_valid=False,
                reason=f"Error: {str(e)}"
            )
    
    # =========================================================================
    # SLIPPAGE PROTECTION
    # =========================================================================
    
    def check_slippage(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        order_book: Optional[Dict[str, Any]] = None
    ) -> SlippageCheck:
        """
        Check if order can be executed within slippage tolerance.
        
        Analyzes order book depth to estimate execution price
        and compares expected slippage to threshold.
        
        Args:
            symbol: Trading pair symbol
            side: 'BUY' or 'SELL'
            quantity: Order quantity
            order_book: Cached order book (fetched if not provided)
            
        Returns:
            SlippageCheck with slippage analysis
        """
        try:
            # Get order book
            if order_book is None:
                if self.redis_cache:
                    order_book = self.redis_cache.get_order_book(symbol)
                
                if order_book is None and self.binance_client:
                    depth = self.binance_client.get_order_book_depth(symbol, limit=100)
                    order_book = depth
            
            if not order_book:
                return SlippageCheck(
                    estimated_slippage_pct=Decimal('999'),
                    is_acceptable=False,
                    sufficient_liquidity=False,
                    estimated_avg_price=Decimal('0'),
                    reason="No order book data available"
                )
            
            # Use asks for BUY, bids for SELL
            levels = order_book['asks'] if side == 'BUY' else order_book['bids']
            
            if not levels:
                return SlippageCheck(
                    estimated_slippage_pct=Decimal('999'),
                    is_acceptable=False,
                    sufficient_liquidity=False,
                    estimated_avg_price=Decimal('0'),
                    reason="Empty order book"
                )
            
            # Calculate execution cost
            remaining_qty = quantity
            total_cost = Decimal('0')
            best_price = levels[0][0]
            
            for price, qty in levels:
                if remaining_qty <= 0:
                    break
                
                fill_qty = min(remaining_qty, qty)
                total_cost += fill_qty * price
                remaining_qty -= fill_qty
            
            # Check liquidity
            if remaining_qty > 0:
                return SlippageCheck(
                    estimated_slippage_pct=Decimal('100'),
                    is_acceptable=False,
                    sufficient_liquidity=False,
                    estimated_avg_price=Decimal('0'),
                    reason=f"Insufficient liquidity: {remaining_qty} remaining"
                )
            
            # Calculate average price and slippage
            avg_price = total_cost / quantity
            slippage = abs(avg_price - best_price)
            slippage_pct = (slippage / best_price) * 100
            
            # Check against threshold
            is_acceptable = slippage_pct <= (self.max_slippage_pct * 100)
            
            reason = "Slippage acceptable" if is_acceptable else \
                     f"Slippage {slippage_pct:.4f}% exceeds max {self.max_slippage_pct * 100}%"
            
            logger.info(
                f"Slippage check for {side} {quantity} {symbol}: "
                f"{slippage_pct:.4f}% ({'OK' if is_acceptable else 'TOO HIGH'})"
            )
            
            return SlippageCheck(
                estimated_slippage_pct=slippage_pct,
                is_acceptable=is_acceptable,
                sufficient_liquidity=True,
                estimated_avg_price=avg_price,
                reason=reason
            )
            
        except Exception as e:
            logger.error(f"Error checking slippage: {e}")
            return SlippageCheck(
                estimated_slippage_pct=Decimal('999'),
                is_acceptable=False,
                sufficient_liquidity=False,
                estimated_avg_price=Decimal('0'),
                reason=f"Error: {str(e)}"
            )
    
    # =========================================================================
    # TRAILING STOP MANAGEMENT
    # =========================================================================
    
    def update_trailing_stops(self, current_prices: Dict[str, Decimal]) -> int:
        """
        Update trailing stops for all open positions.
        
        Args:
            current_prices: Dict of symbol -> current price
            
        Returns:
            Number of positions updated
        """
        updated_count = 0
        
        try:
            # Get all open positions
            open_positions = Position.objects.filter(status=Position.Status.OPEN)
            
            for position in open_positions:
                symbol = position.symbol
                
                if symbol not in current_prices:
                    continue
                
                current_price = current_prices[symbol]
                
                # Update unrealized PnL
                position.update_unrealized_pnl(current_price)
                
                # Update trailing stop
                position.update_trailing_stop(current_price, self.trailing_trigger_pct)
                
                # Check if stop is hit
                stop_hit = self._check_stop_hit(position, current_price)
                
                if stop_hit:
                    logger.warning(
                        f"Stop hit for {position.symbol}: "
                        f"price {current_price} vs stop {position.current_stop}"
                    )
                    # Don't close here - let the strategy coordinator handle it
                
                updated_count += 1
            
            return updated_count
            
        except Exception as e:
            logger.error(f"Error updating trailing stops: {e}")
            return 0
    
    def _check_stop_hit(self, position: Position, current_price: Decimal) -> bool:
        """Check if stop-loss is triggered."""
        if position.side == Trade.Side.BUY:
            return current_price <= position.current_stop
        else:  # SELL (short)
            return current_price >= position.current_stop
    
    def get_stop_loss_price(
        self,
        entry_price: Decimal,
        side: str,
        atr: Optional[Decimal] = None,
        risk_multiple: Decimal = Decimal('2')
    ) -> Decimal:
        """
        Calculate initial stop-loss price.
        
        Uses ATR (Average True Range) if available, otherwise uses
        a percentage-based stop.
        
        Args:
            entry_price: Trade entry price
            side: 'BUY' or 'SELL'
            atr: Average True Range value (optional)
            risk_multiple: ATR multiplier for stop distance
            
        Returns:
            Stop-loss price
        """
        if atr and atr > 0:
            stop_distance = atr * risk_multiple
        else:
            # Default to 1% stop distance
            stop_distance = entry_price * Decimal('0.01')
        
        if side == 'BUY':
            return entry_price - stop_distance
        else:  # SELL
            return entry_price + stop_distance
    
    # =========================================================================
    # CIRCUIT BREAKER
    # =========================================================================
    
    def check_circuit_breaker(self, current_balance: Optional[Decimal] = None) -> Tuple[bool, str]:
        """
        Check if circuit breaker should be triggered.
        
        Triggers if daily drawdown exceeds the threshold (default 5%).
        
        Args:
            current_balance: Current account balance
            
        Returns:
            (should_trigger, reason)
        """
        try:
            # Get today's risk state
            risk_state = RiskState.get_or_create_today()
            
            if risk_state.system_status == RiskState.SystemStatus.PAUSED:
                return True, f"System already paused: {risk_state.pause_reason}"
            
            # Get current balance
            if current_balance is None and self.binance_client:
                current_balance = self.binance_client.get_account_balance('USDT')
            
            if current_balance is None:
                return False, "Unable to check balance"
            
            # Update balance in risk state
            risk_state.update_balance(current_balance)
            
            # Check drawdown
            if risk_state.drawdown_pct >= (self.daily_drawdown_limit * 100):
                reason = (
                    f"Daily drawdown {risk_state.drawdown_pct:.2f}% "
                    f"exceeded limit {self.daily_drawdown_limit * 100}%"
                )
                
                logger.critical(f"CIRCUIT BREAKER TRIGGERED: {reason}")
                
                return True, reason
            
            return False, ""
            
        except Exception as e:
            logger.error(f"Error checking circuit breaker: {e}")
            return False, f"Error: {str(e)}"
    
    def trigger_circuit_breaker(self, reason: str = "Manual trigger") -> bool:
        """
        Trigger the circuit breaker - pause all trading.
        
        Actions:
        1. Update risk state to PAUSED
        2. Cancel all open orders
        3. Update Redis system status
        
        Returns:
            True if successful
        """
        try:
            logger.critical(f"TRIGGERING CIRCUIT BREAKER: {reason}")
            
            # Update risk state
            risk_state = RiskState.get_or_create_today()
            risk_state.trigger_circuit_breaker(reason)
            
            # Cancel all open orders
            if self.binance_client:
                for symbol in settings.TRADING_PAIRS:
                    try:
                        self.binance_client.cancel_all_orders(symbol)
                    except Exception as e:
                        logger.error(f"Error cancelling orders for {symbol}: {e}")
            
            # Update Redis status
            if self.redis_cache:
                self.redis_cache.set_system_status('PAUSED', reason)
            
            logger.info("Circuit breaker activated successfully")
            return True
            
        except Exception as e:
            logger.error(f"Error triggering circuit breaker: {e}")
            return False
    
    def is_trading_allowed(self) -> Tuple[bool, str]:
        """
        Check if trading is currently allowed.
        
        Returns:
            (is_allowed, reason)
        """
        try:
            # Check Redis status first (fastest)
            if self.redis_cache:
                if not self.redis_cache.is_trading_active():
                    status = self.redis_cache.get_system_status()
                    return False, status.get('reason', 'System paused')
            
            # Check database risk state
            risk_state = RiskState.get_or_create_today()
            
            if risk_state.system_status == RiskState.SystemStatus.PAUSED:
                return False, risk_state.pause_reason
            
            if risk_state.system_status == RiskState.SystemStatus.EMERGENCY_STOP:
                return False, "Emergency stop active"
            
            return True, ""
            
        except Exception as e:
            logger.error(f"Error checking trading status: {e}")
            return True, ""  # Default to allowing trades if check fails
    
    # =========================================================================
    # RISK METRICS
    # =========================================================================
    
    def get_current_risk_metrics(self) -> Dict[str, Any]:
        """
        Get current risk metrics summary.
        
        Returns:
            Dict with current risk state
        """
        try:
            risk_state = RiskState.get_or_create_today()
            
            # Get open positions summary
            open_positions = Position.objects.filter(status=Position.Status.OPEN)
            total_exposure = sum(p.quantity * p.entry_price for p in open_positions)
            total_unrealized_pnl = sum(p.unrealized_pnl for p in open_positions)
            
            return {
                'date': str(risk_state.date),
                'system_status': risk_state.system_status,
                'starting_balance': float(risk_state.starting_balance),
                'current_balance': float(risk_state.current_balance),
                'daily_pnl': float(risk_state.daily_pnl),
                'daily_pnl_pct': float(risk_state.daily_pnl_pct),
                'drawdown_pct': float(risk_state.drawdown_pct),
                'max_drawdown_pct': float(risk_state.max_drawdown_pct),
                'total_trades': risk_state.total_trades,
                'win_rate': (
                    risk_state.winning_trades / risk_state.total_trades * 100
                    if risk_state.total_trades > 0 else 0
                ),
                'open_positions': open_positions.count(),
                'total_exposure': float(total_exposure),
                'unrealized_pnl': float(total_unrealized_pnl),
            }
            
        except Exception as e:
            logger.error(f"Error getting risk metrics: {e}")
            return {}

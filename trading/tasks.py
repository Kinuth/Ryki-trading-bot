"""
Celery tasks for the trading system.
Handles background processing, trade loops, and periodic monitoring.
"""
import logging
from decimal import Decimal
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from celery import shared_task
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger('trading')


# =========================================================================
# CORE TRADING TASKS
# =========================================================================

@shared_task(bind=True, max_retries=3)
def strategy_tick(self):
    """
    Main strategy loop - runs every second.
    Evaluates all symbols for trading signals and executes validated signals.
    """
    try:
        from trading.services.strategy_coordinator import StrategyCoordinator
        from trading.services.redis_cache import RedisCache
        
        cache = RedisCache()
        
        # Check if trading is active
        if not cache.is_trading_active():
            return {'status': 'paused'}
        
        coordinator = StrategyCoordinator()
        signals = coordinator.evaluate_all_symbols()
        
        executed_trades = []
        for signal in signals:
            if signal.is_valid:
                # Execute the trade
                result = execute_trade.delay(signal.to_dict())
                executed_trades.append({
                    'symbol': signal.symbol,
                    'action': signal.action.value,
                    'task_id': result.id
                })
                
                # Broadcast signal to dashboard
                broadcast_to_dashboard('signal_generated', signal.to_dict())
        
        return {
            'status': 'success',
            'signals_evaluated': len(settings.TRADING_PAIRS),
            'signals_valid': len(signals),
            'trades_initiated': len(executed_trades)
        }
        
    except Exception as e:
        logger.error(f"Strategy tick error: {e}", exc_info=True)
        raise self.retry(exc=e, countdown=5)


@shared_task(bind=True, max_retries=3)
def execute_trade(self, signal_dict: Dict[str, Any]):
    """
    Execute a trading signal by placing orders on Binance.
    
    Handles the full order lifecycle:
    1. Place order
    2. Create Trade record
    3. Create Position record
    4. Broadcast updates
    
    Args:
        signal_dict: Signal data from StrategyCoordinator
    """
    try:
        from trading.services.binance_client import BinanceClient
        from trading.services.redis_cache import RedisCache
        from trading.models import Trade, Position
        
        symbol = signal_dict['symbol']
        action = signal_dict['action']
        quantity = Decimal(signal_dict['quantity'])
        entry_price = Decimal(signal_dict['entry_price'])
        stop_loss = Decimal(signal_dict['stop_loss'])
        take_profit = Decimal(signal_dict['take_profit']) if signal_dict.get('take_profit') else None
        
        client = BinanceClient()
        cache = RedisCache()
        
        logger.info(f"Executing trade: {action} {quantity} {symbol} @ {entry_price}")
        
        # Determine order side
        if action in ['BUY', 'CLOSE_SHORT']:
            side = 'BUY'
        else:  # SELL, CLOSE_LONG
            side = 'SELL'
        
        # Place the order (using LIMIT order for better execution)
        formatted_price = client.format_price(symbol, entry_price)
        formatted_qty = client.format_quantity(symbol, quantity)
        
        order = client.place_limit_order(
            symbol=symbol,
            side=side,
            quantity=formatted_qty,
            price=formatted_price
        )
        
        # Create Trade record
        trade = Trade.objects.create(
            binance_order_id=str(order['orderId']),
            binance_client_order_id=order.get('clientOrderId', ''),
            symbol=symbol,
            side=side,
            order_type=order['type'],
            requested_quantity=formatted_qty,
            requested_price=formatted_price,
            expected_price=entry_price,
            status=Trade.Status.PENDING,
            vpa_signal=signal_dict.get('vpa_pattern', ''),
            three_d_signal=signal_dict.get('three_d_confluence', ''),
            ema_deviation=Decimal(signal_dict.get('ema_deviation', '0')),
            macro_context=signal_dict.get('macro_context', ''),
        )
        
        # Start monitoring the order
        monitor_order.delay(trade.id)
        
        # Clear the signal from cache
        cache.clear_signal(symbol)
        
        # Broadcast trade creation
        broadcast_to_dashboard('trade_update', {
            'id': trade.id,
            'symbol': symbol,
            'side': side,
            'status': 'PENDING',
            'quantity': str(formatted_qty),
            'price': str(formatted_price),
        })
        
        return {
            'status': 'success',
            'trade_id': trade.id,
            'order_id': order['orderId']
        }
        
    except Exception as e:
        logger.error(f"Trade execution error: {e}", exc_info=True)
        broadcast_to_dashboard('trade_update', {
            'symbol': signal_dict.get('symbol'),
            'status': 'FAILED',
            'error': str(e)
        })
        raise self.retry(exc=e, countdown=5)


@shared_task(bind=True, max_retries=10)
def monitor_order(self, trade_id: int):
    """
    Monitor an order until it's filled or cancelled.
    
    Handles partial fills by updating the Trade record progressively.
    Creates Position record once fully filled.
    
    Args:
        trade_id: ID of the Trade record to monitor
    """
    try:
        from trading.services.binance_client import BinanceClient
        from trading.models import Trade, Position, RiskState
        
        trade = Trade.objects.get(id=trade_id)
        client = BinanceClient()
        
        # Get order status from Binance
        order = client.get_order(trade.symbol, int(trade.binance_order_id))
        
        status = order['status']
        filled_qty = Decimal(order['executedQty'])
        avg_price = Decimal(order.get('avgPrice', '0') or order.get('price', '0'))
        
        # Update trade record
        trade.filled_quantity = filled_qty
        trade.average_price = avg_price if avg_price > 0 else trade.requested_price
        
        if status == 'FILLED':
            trade.status = Trade.Status.FILLED
            trade.filled_at = timezone.now()
            trade.execution_price = avg_price
            trade.calculate_slippage()
            
            # Create position if this is an entry trade
            if trade.side in ['BUY', 'SELL'] and not hasattr(trade, 'positions'):
                position = create_position_from_trade(trade)
                
                # Broadcast position creation
                broadcast_to_dashboard('position_update', {
                    'id': position.id,
                    'symbol': position.symbol,
                    'side': position.side,
                    'quantity': str(position.quantity),
                    'entry_price': str(position.entry_price),
                    'status': 'OPEN',
                })
            
            # Update risk state
            risk_state = RiskState.get_or_create_today()
            risk_state.total_trades += 1
            risk_state.save()
            
            # Broadcast fill notification
            broadcast_to_dashboard('order_fill', {
                'trade_id': trade.id,
                'symbol': trade.symbol,
                'side': trade.side,
                'quantity': str(filled_qty),
                'price': str(avg_price),
                'status': 'FILLED',
            })
            
        elif status == 'PARTIALLY_FILLED':
            trade.status = Trade.Status.PARTIALLY_FILLED
            
            # Log partial fill
            logger.info(
                f"Partial fill for {trade.symbol}: "
                f"{filled_qty}/{trade.requested_quantity} @ {avg_price}"
            )
            
            # Broadcast partial fill
            broadcast_to_dashboard('order_fill', {
                'trade_id': trade.id,
                'symbol': trade.symbol,
                'status': 'PARTIAL',
                'filled': str(filled_qty),
                'remaining': str(trade.requested_quantity - filled_qty),
            })
            
            # Schedule another check
            self.retry(countdown=2)
            
        elif status == 'CANCELED':
            trade.status = Trade.Status.CANCELLED
            
            broadcast_to_dashboard('order_fill', {
                'trade_id': trade.id,
                'symbol': trade.symbol,
                'status': 'CANCELLED',
            })
            
        elif status == 'REJECTED':
            trade.status = Trade.Status.REJECTED
            
            broadcast_to_dashboard('order_fill', {
                'trade_id': trade.id,
                'symbol': trade.symbol,
                'status': 'REJECTED',
            })
        
        else:
            # Still pending, check again
            self.retry(countdown=2)
        
        trade.save()
        
        return {
            'status': trade.status,
            'filled_qty': str(filled_qty),
            'avg_price': str(avg_price),
        }
        
    except Trade.DoesNotExist:
        logger.error(f"Trade {trade_id} not found")
        return {'status': 'error', 'message': 'Trade not found'}
        
    except Exception as e:
        logger.error(f"Order monitoring error: {e}", exc_info=True)
        raise self.retry(exc=e, countdown=5)


def create_position_from_trade(trade: 'Trade') -> 'Position':
    """Create a Position record from a filled trade."""
    from trading.models import Position
    from trading.services.risk_manager import RiskManager
    from trading.services.binance_client import BinanceClient
    from trading.services.redis_cache import RedisCache
    
    rm = RiskManager(
        binance_client=BinanceClient(),
        redis_cache=RedisCache()
    )
    
    # Calculate stop loss
    stop_loss = rm.get_stop_loss_price(
        entry_price=trade.average_price or trade.requested_price,
        side=trade.side,
    )
    
    position = Position.objects.create(
        entry_trade=trade,
        symbol=trade.symbol,
        side=trade.side,
        quantity=trade.filled_quantity,
        entry_price=trade.average_price or trade.requested_price,
        initial_stop=stop_loss,
        current_stop=stop_loss,
        status=Position.Status.OPEN,
    )
    
    return position


# =========================================================================
# MONITORING TASKS
# =========================================================================

@shared_task
def monitor_positions():
    """
    Monitor all open positions - runs every 5 seconds.
    Updates trailing stops and checks for stop/take profit triggers.
    """
    try:
        from trading.services.risk_manager import RiskManager
        from trading.services.binance_client import BinanceClient
        from trading.services.redis_cache import RedisCache
        from trading.models import Position, Trade
        
        client = BinanceClient()
        cache = RedisCache()
        rm = RiskManager(binance_client=client, redis_cache=cache)
        
        # Get current prices for all trading pairs
        current_prices = {}
        for symbol in settings.TRADING_PAIRS:
            price = cache.get_price(symbol)
            if price is None:
                try:
                    price = client.get_ticker_price(symbol)
                    cache.set_price(symbol, price)
                except:
                    continue
            current_prices[symbol] = price
        
        # Update trailing stops
        updated = rm.update_trailing_stops(current_prices)
        
        # Check for positions that need to be closed
        positions = Position.objects.filter(status=Position.Status.OPEN)
        
        positions_to_close = []
        for position in positions:
            if position.symbol not in current_prices:
                continue
            
            current_price = current_prices[position.symbol]
            
            # Check stop loss
            if position.side == Trade.Side.BUY:
                if current_price <= position.current_stop:
                    positions_to_close.append((position, 'STOP_LOSS'))
                elif position.take_profit and current_price >= position.take_profit:
                    positions_to_close.append((position, 'TAKE_PROFIT'))
            else:
                if current_price >= position.current_stop:
                    positions_to_close.append((position, 'STOP_LOSS'))
                elif position.take_profit and current_price <= position.take_profit:
                    positions_to_close.append((position, 'TAKE_PROFIT'))
        
        # Close triggered positions
        for position, reason in positions_to_close:
            close_position.delay(position.id, reason)
        
        # Broadcast price updates
        for symbol, price in current_prices.items():
            broadcast_to_dashboard('price_update', {
                'symbol': symbol,
                'price': str(price),
                'timestamp': timezone.now().isoformat(),
            })
        
        return {
            'positions_monitored': len(positions),
            'trailing_stops_updated': updated,
            'positions_to_close': len(positions_to_close),
        }
        
    except Exception as e:
        logger.error(f"Position monitoring error: {e}", exc_info=True)
        return {'status': 'error', 'message': str(e)}


@shared_task
def close_position(position_id: int, reason: str):
    """
    Close a position by placing an exit order.
    
    Args:
        position_id: ID of the Position to close
        reason: Reason for closing (STOP_LOSS, TAKE_PROFIT, TRAILING_STOP, MANUAL)
    """
    try:
        from trading.services.binance_client import BinanceClient
        from trading.models import Position, Trade
        
        position = Position.objects.get(id=position_id)
        
        if position.status != Position.Status.OPEN:
            return {'status': 'already_closed'}
        
        client = BinanceClient()
        
        # Determine exit side (opposite of entry)
        exit_side = 'SELL' if position.side == Trade.Side.BUY else 'BUY'
        
        # Place market order for immediate exit
        order = client.place_market_order(
            symbol=position.symbol,
            side=exit_side,
            quantity=position.quantity
        )
        
        # Create exit trade record
        exit_trade = Trade.objects.create(
            binance_order_id=str(order['orderId']),
            symbol=position.symbol,
            side=exit_side,
            order_type='MARKET',
            requested_quantity=position.quantity,
            status=Trade.Status.PENDING,
            macro_context=f"Position close: {reason}",
        )
        
        # Update position
        position.exit_trade = exit_trade
        position.status = Position.Status.CLOSED
        position.close_reason = reason
        position.closed_at = timezone.now()
        position.save()
        
        # Monitor the exit order
        monitor_order.delay(exit_trade.id)
        
        # Broadcast position close
        broadcast_to_dashboard('position_update', {
            'id': position.id,
            'symbol': position.symbol,
            'status': 'CLOSED',
            'reason': reason,
        })
        
        return {
            'status': 'success',
            'position_id': position_id,
            'reason': reason,
            'exit_order_id': order['orderId'],
        }
        
    except Position.DoesNotExist:
        return {'status': 'error', 'message': 'Position not found'}
    except Exception as e:
        logger.error(f"Error closing position {position_id}: {e}", exc_info=True)
        return {'status': 'error', 'message': str(e)}


@shared_task
def check_circuit_breaker():
    """
    Check if circuit breaker should be triggered - runs every minute.
    """
    try:
        from trading.services.risk_manager import RiskManager
        from trading.services.binance_client import BinanceClient
        from trading.services.redis_cache import RedisCache
        
        rm = RiskManager(
            binance_client=BinanceClient(),
            redis_cache=RedisCache()
        )
        
        should_trigger, reason = rm.check_circuit_breaker()
        
        if should_trigger:
            rm.trigger_circuit_breaker(reason)
            
            broadcast_to_dashboard('system_status_update', {
                'status': 'PAUSED',
                'reason': reason,
                'timestamp': timezone.now().isoformat(),
            })
            
            return {
                'triggered': True,
                'reason': reason
            }
        
        return {'triggered': False}
        
    except Exception as e:
        logger.error(f"Circuit breaker check error: {e}", exc_info=True)
        return {'status': 'error', 'message': str(e)}


@shared_task
def update_risk_state():
    """
    Update risk state with current balance - runs every minute.
    """
    try:
        from trading.services.binance_client import BinanceClient
        from trading.services.risk_manager import RiskManager
        from trading.services.redis_cache import RedisCache
        
        client = BinanceClient()
        rm = RiskManager(
            binance_client=client,
            redis_cache=RedisCache()
        )
        
        # Get and update metrics
        metrics = rm.get_current_risk_metrics()
        
        # Broadcast to dashboard
        broadcast_to_dashboard('risk_update', metrics)
        
        return metrics
        
    except Exception as e:
        logger.error(f"Risk state update error: {e}", exc_info=True)
        return {'status': 'error', 'message': str(e)}


# =========================================================================
# ECONOMIC CALENDAR TASKS
# =========================================================================

@shared_task
def fetch_economic_events():
    """
    Fetch upcoming economic events from APIs.
    Runs every hour to keep events fresh.
    """
    try:
        from trading.models import EconomicEvent
        import httpx
        
        events_added = 0
        
        # Fetch from Investing.com (if API key configured)
        if settings.INVESTING_COM_API_KEY:
            # Note: Actual implementation depends on Investing.com API structure
            logger.info("Fetching from Investing.com API...")
            # events_added += fetch_investing_events()
        
        # Fetch from TradingEconomics (if API key configured)
        if settings.TRADING_ECONOMICS_API_KEY:
            logger.info("Fetching from TradingEconomics API...")
            # events_added += fetch_trading_economics_events()
        
        return {'events_added': events_added}
        
    except Exception as e:
        logger.error(f"Economic events fetch error: {e}", exc_info=True)
        return {'status': 'error', 'message': str(e)}


# =========================================================================
# HELPER FUNCTIONS
# =========================================================================

def broadcast_to_dashboard(message_type: str, data: Dict[str, Any]):
    """
    Broadcast a message to all connected dashboard clients.
    
    Args:
        message_type: Type of message (e.g., 'price_update', 'trade_update')
        data: Data to send
    """
    try:
        channel_layer = get_channel_layer()
        
        async_to_sync(channel_layer.group_send)(
            'trading_dashboard',
            {
                'type': message_type,
                'data': data
            }
        )
    except Exception as e:
        logger.warning(f"Broadcast error: {e}")

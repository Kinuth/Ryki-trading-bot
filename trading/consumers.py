"""
WebSocket consumers for real-time dashboard updates.
Handles live price streaming and trade notifications.
"""
import json
import logging
import asyncio
from decimal import Decimal
from typing import Dict, Any
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.db import database_sync_to_async
from django.conf import settings

logger = logging.getLogger('trading')


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder for Decimal values."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)


class DashboardConsumer(AsyncJsonWebsocketConsumer):
    """
    WebSocket consumer for the trading dashboard.
    
    Provides real-time updates for:
    - Price ticks across all symbols
    - Trade executions and fills
    - Position updates
    - Risk metrics
    - System status
    """
    
    async def connect(self):
        """Handle WebSocket connection."""
        self.room_name = 'dashboard'
        self.room_group_name = f'trading_{self.room_name}'
        
        # Join room group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        
        await self.accept()
        
        # Send initial state
        await self.send_initial_state()
        
        logger.info(f"WebSocket connected: {self.channel_name}")
    
    async def disconnect(self, close_code):
        """Handle WebSocket disconnection."""
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
        logger.info(f"WebSocket disconnected: {self.channel_name}")
    
    async def receive_json(self, content):
        """
        Handle incoming messages from the client.
        
        Supported message types:
        - subscribe: Subscribe to specific symbols
        - command: Execute trading commands
        """
        msg_type = content.get('type')
        
        if msg_type == 'subscribe':
            symbols = content.get('symbols', [])
            await self.handle_subscribe(symbols)
        
        elif msg_type == 'command':
            command = content.get('command')
            params = content.get('params', {})
            await self.handle_command(command, params)
        
        elif msg_type == 'ping':
            await self.send_json({'type': 'pong'})
    
    async def handle_subscribe(self, symbols: list):
        """Handle symbol subscription."""
        self.subscribed_symbols = symbols
        await self.send_json({
            'type': 'subscribed',
            'symbols': symbols
        })
    
    async def handle_command(self, command: str, params: dict):
        """Handle trading commands from the client."""
        if command == 'pause':
            # Pause trading
            await database_sync_to_async(self._pause_trading)(params.get('reason', 'Client request'))
            await self.send_json({
                'type': 'command_result',
                'command': command,
                'success': True
            })
        
        elif command == 'resume':
            # Resume trading
            await database_sync_to_async(self._resume_trading)()
            await self.send_json({
                'type': 'command_result',
                'command': command,
                'success': True
            })
        
        elif command == 'get_positions':
            positions = await database_sync_to_async(self._get_open_positions)()
            await self.send_json({
                'type': 'positions',
                'data': positions
            })
    
    async def send_initial_state(self):
        """Send initial state when client connects."""
        # Get current prices
        prices = await database_sync_to_async(self._get_current_prices)()
        
        # Get open positions
        positions = await database_sync_to_async(self._get_open_positions)()
        
        # Get risk metrics
        risk_metrics = await database_sync_to_async(self._get_risk_metrics)()
        
        # Get system status
        system_status = await database_sync_to_async(self._get_system_status)()
        
        await self.send_json({
            'type': 'initial_state',
            'data': {
                'prices': prices,
                'positions': positions,
                'risk_metrics': risk_metrics,
                'system_status': system_status,
                'trading_pairs': settings.TRADING_PAIRS,
            }
        })
    
    # =========================================================================
    # BROADCAST MESSAGE HANDLERS
    # These are called via channel_layer.group_send()
    # =========================================================================
    
    async def price_update(self, event):
        """Broadcast price update to client."""
        await self.send_json({
            'type': 'price_update',
            'data': event['data']
        })
    
    async def trade_update(self, event):
        """Broadcast trade update to client."""
        await self.send_json({
            'type': 'trade_update',
            'data': event['data']
        })
    
    async def position_update(self, event):
        """Broadcast position update to client."""
        await self.send_json({
            'type': 'position_update',
            'data': event['data']
        })
    
    async def signal_generated(self, event):
        """Broadcast new trading signal to client."""
        await self.send_json({
            'type': 'signal',
            'data': event['data']
        })
    
    async def risk_update(self, event):
        """Broadcast risk metrics update to client."""
        await self.send_json({
            'type': 'risk_update',
            'data': event['data']
        })
    
    async def system_status_update(self, event):
        """Broadcast system status change to client."""
        await self.send_json({
            'type': 'system_status',
            'data': event['data']
        })
    
    async def order_fill(self, event):
        """Broadcast order fill notification."""
        await self.send_json({
            'type': 'order_fill',
            'data': event['data']
        })
    
    # =========================================================================
    # SYNC HELPER METHODS
    # =========================================================================
    
    def _get_current_prices(self) -> Dict[str, str]:
        """Get current prices from Redis cache."""
        from trading.services.redis_cache import RedisCache
        
        cache = RedisCache()
        prices = {}
        
        for symbol in settings.TRADING_PAIRS:
            price = cache.get_price(symbol)
            if price:
                prices[symbol] = str(price)
        
        return prices
    
    def _get_open_positions(self) -> list:
        """Get all open positions."""
        from trading.models import Position
        
        positions = Position.objects.filter(status=Position.Status.OPEN)
        
        return [
            {
                'id': p.id,
                'symbol': p.symbol,
                'side': p.side,
                'quantity': str(p.quantity),
                'entry_price': str(p.entry_price),
                'current_price': str(p.current_price) if p.current_price else None,
                'unrealized_pnl': str(p.unrealized_pnl),
                'unrealized_pnl_pct': str(p.unrealized_pnl_pct),
                'current_stop': str(p.current_stop),
                'trailing_activated': p.trailing_activated,
                'opened_at': p.opened_at.isoformat(),
            }
            for p in positions
        ]
    
    def _get_risk_metrics(self) -> dict:
        """Get current risk metrics."""
        from trading.services.risk_manager import RiskManager
        from trading.services.binance_client import BinanceClient
        from trading.services.redis_cache import RedisCache
        
        try:
            rm = RiskManager(
                binance_client=BinanceClient(),
                redis_cache=RedisCache()
            )
            return rm.get_current_risk_metrics()
        except:
            return {}
    
    def _get_system_status(self) -> dict:
        """Get current system status."""
        from trading.services.redis_cache import RedisCache
        
        try:
            cache = RedisCache()
            return cache.get_system_status()
        except:
            return {'status': 'UNKNOWN'}
    
    def _pause_trading(self, reason: str):
        """Pause trading system."""
        from trading.services.risk_manager import RiskManager
        from trading.services.binance_client import BinanceClient
        from trading.services.redis_cache import RedisCache
        
        rm = RiskManager(
            binance_client=BinanceClient(),
            redis_cache=RedisCache()
        )
        rm.trigger_circuit_breaker(reason)
    
    def _resume_trading(self):
        """Resume trading system."""
        from trading.services.redis_cache import RedisCache
        from trading.models import RiskState
        
        cache = RedisCache()
        cache.set_system_status('ACTIVE', '')
        
        # Update database
        risk_state = RiskState.get_or_create_today()
        risk_state.system_status = RiskState.SystemStatus.ACTIVE
        risk_state.pause_reason = ''
        risk_state.save()


class PriceStreamConsumer(AsyncJsonWebsocketConsumer):
    """
    WebSocket consumer for real-time price streaming.
    Receives price updates from Binance WebSocket and broadcasts to clients.
    """
    
    async def connect(self):
        """Handle WebSocket connection."""
        self.room_group_name = 'price_stream'
        
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        
        await self.accept()
    
    async def disconnect(self, close_code):
        """Handle WebSocket disconnection."""
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
    
    async def price_tick(self, event):
        """Broadcast price tick to connected clients."""
        await self.send_json({
            'type': 'tick',
            'symbol': event['symbol'],
            'price': event['price'],
            'timestamp': event['timestamp']
        })
    
    async def orderbook_update(self, event):
        """Broadcast order book update."""
        await self.send_json({
            'type': 'orderbook',
            'symbol': event['symbol'],
            'bids': event['bids'][:5],  # Top 5 levels
            'asks': event['asks'][:5],
            'timestamp': event['timestamp']
        })

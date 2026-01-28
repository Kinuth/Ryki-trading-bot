"""
Binance WebSocket Manager.
Handles real-time streaming of klines, order book, and trade data.
"""
import asyncio
import logging
from decimal import Decimal
from typing import Optional, Dict, Any, List, Callable
from django.conf import settings
from binance import AsyncClient, BinanceSocketManager
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

logger = logging.getLogger('trading')


class WebSocketManager:
    """
    Manages Binance WebSocket connections for real-time data streaming.
    
    Streams:
    - Kline/Candlestick data for strategy analysis
    - Order book depth for slippage estimation
    - User data stream for order updates
    """
    
    def __init__(self):
        """Initialize WebSocket manager."""
        self.client: Optional[AsyncClient] = None
        self.bm: Optional[BinanceSocketManager] = None
        self.sockets: Dict[str, Any] = {}
        self.running = False
        
        self.api_key = settings.BINANCE_API_KEY
        self.api_secret = settings.BINANCE_API_SECRET
        self.testnet = settings.BINANCE_TESTNET
        
        # Redis cache for storing data
        self._redis_cache = None
    
    @property
    def redis_cache(self):
        """Lazy load Redis cache."""
        if self._redis_cache is None:
            from trading.services.redis_cache import RedisCache
            self._redis_cache = RedisCache()
        return self._redis_cache
    
    async def start(self):
        """Start the WebSocket connections."""
        logger.info("Starting Binance WebSocket manager...")
        
        # Initialize async client
        self.client = await AsyncClient.create(
            api_key=self.api_key,
            api_secret=self.api_secret,
            testnet=self.testnet
        )
        
        # Initialize socket manager
        self.bm = BinanceSocketManager(self.client)
        
        self.running = True
        
        # Start streams for each trading pair
        await self._start_streams()
        
        logger.info("WebSocket manager started successfully")
    
    async def stop(self):
        """Stop all WebSocket connections."""
        logger.info("Stopping WebSocket manager...")
        
        self.running = False
        
        # Close all sockets
        for name, socket in self.sockets.items():
            try:
                await socket.__aexit__(None, None, None)
            except Exception as e:
                logger.warning(f"Error closing socket {name}: {e}")
        
        self.sockets.clear()
        
        # Close client
        if self.client:
            await self.client.close_connection()
        
        logger.info("WebSocket manager stopped")
    
    async def _start_streams(self):
        """Start data streams for all trading pairs."""
        tasks = []
        
        for symbol in settings.TRADING_PAIRS:
            # Start kline stream (1-minute candles)
            tasks.append(self._start_kline_stream(symbol, '1m'))
            
            # Start depth stream
            tasks.append(self._start_depth_stream(symbol))
        
        # Start user data stream for order updates
        if self.api_key:
            tasks.append(self._start_user_data_stream())
        
        await asyncio.gather(*tasks)
    
    async def _start_kline_stream(self, symbol: str, interval: str):
        """
        Start kline/candlestick stream for a symbol.
        
        Streams real-time OHLCV data and caches to Redis.
        """
        try:
            socket = self.bm.kline_socket(symbol, interval)
            self.sockets[f'kline_{symbol}_{interval}'] = socket
            
            async with socket as stream:
                while self.running:
                    try:
                        msg = await asyncio.wait_for(stream.recv(), timeout=30)
                        await self._handle_kline_message(msg)
                    except asyncio.TimeoutError:
                        continue
                    except Exception as e:
                        logger.error(f"Kline stream error for {symbol}: {e}")
                        await asyncio.sleep(1)
                        
        except Exception as e:
            logger.error(f"Failed to start kline stream for {symbol}: {e}")
    
    async def _start_depth_stream(self, symbol: str):
        """
        Start order book depth stream for a symbol.
        
        Streams real-time bid/ask data for slippage estimation.
        """
        try:
            socket = self.bm.depth_socket(symbol, depth=BinanceSocketManager.WEBSOCKET_DEPTH_20)
            self.sockets[f'depth_{symbol}'] = socket
            
            async with socket as stream:
                while self.running:
                    try:
                        msg = await asyncio.wait_for(stream.recv(), timeout=30)
                        await self._handle_depth_message(symbol, msg)
                    except asyncio.TimeoutError:
                        continue
                    except Exception as e:
                        logger.error(f"Depth stream error for {symbol}: {e}")
                        await asyncio.sleep(1)
                        
        except Exception as e:
            logger.error(f"Failed to start depth stream for {symbol}: {e}")
    
    async def _start_user_data_stream(self):
        """
        Start user data stream for order and account updates.
        
        Receives notifications about order fills, cancellations, etc.
        """
        try:
            socket = self.bm.user_socket()
            self.sockets['user_data'] = socket
            
            async with socket as stream:
                while self.running:
                    try:
                        msg = await asyncio.wait_for(stream.recv(), timeout=60)
                        await self._handle_user_data_message(msg)
                    except asyncio.TimeoutError:
                        continue
                    except Exception as e:
                        logger.error(f"User data stream error: {e}")
                        await asyncio.sleep(1)
                        
        except Exception as e:
            logger.error(f"Failed to start user data stream: {e}")
    
    async def _handle_kline_message(self, msg: Dict[str, Any]):
        """
        Handle incoming kline message.
        
        Caches price to Redis and broadcasts to dashboard.
        """
        if 'e' not in msg or msg['e'] != 'kline':
            return
        
        kline = msg['k']
        symbol = kline['s']
        
        # Extract data
        data = {
            'symbol': symbol,
            'interval': kline['i'],
            'open_time': kline['t'],
            'open': kline['o'],
            'high': kline['h'],
            'low': kline['l'],
            'close': kline['c'],
            'volume': kline['v'],
            'close_time': kline['T'],
            'is_closed': kline['x'],
        }
        
        # Cache current price (every 100ms is handled by rate limiting)
        price = Decimal(kline['c'])
        self.redis_cache.set_price(symbol, price)
        
        # If candle is closed, cache it
        if kline['x']:  # Candle closed
            kline_data = {
                'open_time': kline['t'],
                'open': kline['o'],
                'high': kline['h'],
                'low': kline['l'],
                'close': kline['c'],
                'volume': kline['v'],
                'close_time': kline['T'],
            }
            self.redis_cache.append_kline_to_history(symbol, kline['i'], kline_data)
        
        # Broadcast price update
        await self._broadcast_price_update(symbol, price)
    
    async def _handle_depth_message(self, symbol: str, msg: Dict[str, Any]):
        """
        Handle incoming order book depth message.
        
        Caches order book to Redis.
        """
        bids = [(Decimal(p), Decimal(q)) for p, q in msg.get('bids', [])]
        asks = [(Decimal(p), Decimal(q)) for p, q in msg.get('asks', [])]
        
        self.redis_cache.set_order_book(symbol, bids, asks)
    
    async def _handle_user_data_message(self, msg: Dict[str, Any]):
        """
        Handle user data stream messages.
        
        Processes order updates and broadcasts to dashboard.
        """
        event_type = msg.get('e')
        
        if event_type == 'executionReport':
            await self._handle_order_update(msg)
        elif event_type == 'outboundAccountPosition':
            await self._handle_account_update(msg)
    
    async def _handle_order_update(self, msg: Dict[str, Any]):
        """Handle order execution report."""
        from trading.models import Trade
        
        order_id = str(msg['i'])
        status = msg['X']
        symbol = msg['s']
        side = msg['S']
        filled_qty = Decimal(msg['z'])
        avg_price = Decimal(msg['Z']) / filled_qty if filled_qty > 0 else Decimal('0')
        
        logger.info(f"Order update: {symbol} {side} {status} - filled {filled_qty}")
        
        # Update Trade record
        try:
            trade = Trade.objects.get(binance_order_id=order_id)
            trade.filled_quantity = filled_qty
            trade.average_price = avg_price
            
            if status == 'FILLED':
                trade.status = Trade.Status.FILLED
            elif status == 'PARTIALLY_FILLED':
                trade.status = Trade.Status.PARTIALLY_FILLED
            elif status == 'CANCELED':
                trade.status = Trade.Status.CANCELLED
            elif status == 'REJECTED':
                trade.status = Trade.Status.REJECTED
            
            trade.save()
            
            # Broadcast order update
            await self._broadcast_order_update(trade, status)
            
        except Trade.DoesNotExist:
            logger.warning(f"Trade not found for order {order_id}")
    
    async def _handle_account_update(self, msg: Dict[str, Any]):
        """Handle account position update."""
        balances = msg.get('B', [])
        
        for balance in balances:
            asset = balance['a']
            free = Decimal(balance['f'])
            
            if asset == 'USDT':
                logger.info(f"USDT balance updated: {free}")
    
    async def _broadcast_price_update(self, symbol: str, price: Decimal):
        """Broadcast price update to WebSocket clients."""
        try:
            channel_layer = get_channel_layer()
            
            await channel_layer.group_send(
                'trading_dashboard',
                {
                    'type': 'price_update',
                    'data': {
                        'symbol': symbol,
                        'price': str(price),
                    }
                }
            )
            
            await channel_layer.group_send(
                'price_stream',
                {
                    'type': 'price_tick',
                    'symbol': symbol,
                    'price': str(price),
                    'timestamp': int(asyncio.get_event_loop().time() * 1000),
                }
            )
            
        except Exception as e:
            logger.debug(f"Broadcast error: {e}")
    
    async def _broadcast_order_update(self, trade: 'Trade', status: str):
        """Broadcast order update to WebSocket clients."""
        try:
            channel_layer = get_channel_layer()
            
            await channel_layer.group_send(
                'trading_dashboard',
                {
                    'type': 'order_fill',
                    'data': {
                        'trade_id': trade.id,
                        'symbol': trade.symbol,
                        'side': trade.side,
                        'status': status,
                        'filled_qty': str(trade.filled_quantity),
                        'avg_price': str(trade.average_price),
                    }
                }
            )
            
        except Exception as e:
            logger.debug(f"Broadcast error: {e}")


# Global instance
_websocket_manager: Optional[WebSocketManager] = None


def get_websocket_manager() -> WebSocketManager:
    """Get the global WebSocket manager instance."""
    global _websocket_manager
    if _websocket_manager is None:
        _websocket_manager = WebSocketManager()
    return _websocket_manager


async def start_websocket_manager():
    """Start the WebSocket manager."""
    manager = get_websocket_manager()
    await manager.start()


async def stop_websocket_manager():
    """Stop the WebSocket manager."""
    manager = get_websocket_manager()
    await manager.stop()

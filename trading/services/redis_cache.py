"""
Redis cache service for real-time price and order book caching.
Provides zero-latency access to market state for the strategy engine.
"""
import json
import logging
from decimal import Decimal
from typing import Optional, Dict, Any, List
from django.conf import settings
import redis

logger = logging.getLogger('trading')


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder that handles Decimal types."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)


def decimal_decoder(dct):
    """JSON decoder hook for Decimal fields."""
    for key, value in dct.items():
        if isinstance(value, str):
            try:
                dct[key] = Decimal(value)
            except:
                pass
    return dct


class RedisCache:
    """
    Redis-based caching for real-time market data.
    Caches prices at 100ms intervals for strategy engine.
    """
    
    # Key prefixes
    PRICE_KEY = 'price:{symbol}'
    ORDER_BOOK_KEY = 'orderbook:{symbol}'
    KLINE_KEY = 'kline:{symbol}:{interval}'
    EMA_KEY = 'ema:{symbol}:{period}'
    SIGNAL_KEY = 'signal:{symbol}'
    SYSTEM_STATUS_KEY = 'system:status'
    
    def __init__(self):
        """Initialize Redis connection."""
        self.redis_url = settings.REDIS_URL
        self.client = redis.from_url(
            self.redis_url,
            decode_responses=True
        )
        
        # Test connection
        try:
            self.client.ping()
            logger.info("Redis connection established")
        except redis.ConnectionError as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise
    
    # =========================================================================
    # PRICE CACHING
    # =========================================================================
    
    def set_price(self, symbol: str, price: Decimal, ttl: int = 60) -> None:
        """
        Cache current price for a symbol.
        
        Args:
            symbol: Trading pair symbol
            price: Current price
            ttl: Time to live in seconds (default 60s)
        """
        key = self.PRICE_KEY.format(symbol=symbol)
        data = {
            'price': str(price),
            'timestamp': self._get_timestamp()
        }
        self.client.setex(key, ttl, json.dumps(data))
    
    def get_price(self, symbol: str) -> Optional[Decimal]:
        """
        Get cached price for a symbol.
        
        Returns:
            Current price or None if not cached
        """
        key = self.PRICE_KEY.format(symbol=symbol)
        data = self.client.get(key)
        
        if data:
            parsed = json.loads(data)
            return Decimal(parsed['price'])
        return None
    
    def get_prices(self, symbols: List[str]) -> Dict[str, Optional[Decimal]]:
        """Get cached prices for multiple symbols."""
        result = {}
        for symbol in symbols:
            result[symbol] = self.get_price(symbol)
        return result
    
    # =========================================================================
    # ORDER BOOK CACHING
    # =========================================================================
    
    def set_order_book(
        self,
        symbol: str,
        bids: List[tuple],
        asks: List[tuple],
        ttl: int = 1
    ) -> None:
        """
        Cache order book depth.
        
        Args:
            symbol: Trading pair symbol
            bids: List of (price, quantity) tuples
            asks: List of (price, quantity) tuples
            ttl: Time to live in seconds (default 1s for real-time data)
        """
        key = self.ORDER_BOOK_KEY.format(symbol=symbol)
        data = {
            'bids': [[str(p), str(q)] for p, q in bids[:20]],
            'asks': [[str(p), str(q)] for p, q in asks[:20]],
            'timestamp': self._get_timestamp()
        }
        self.client.setex(key, ttl, json.dumps(data))
    
    def get_order_book(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get cached order book.
        
        Returns:
            Dict with 'bids' and 'asks' or None
        """
        key = self.ORDER_BOOK_KEY.format(symbol=symbol)
        data = self.client.get(key)
        
        if data:
            parsed = json.loads(data)
            return {
                'bids': [(Decimal(p), Decimal(q)) for p, q in parsed['bids']],
                'asks': [(Decimal(p), Decimal(q)) for p, q in parsed['asks']],
                'timestamp': parsed['timestamp']
            }
        return None
    
    # =========================================================================
    # KLINE/CANDLESTICK CACHING
    # =========================================================================
    
    def set_latest_kline(
        self,
        symbol: str,
        interval: str,
        kline: Dict[str, Any],
        ttl: int = 60
    ) -> None:
        """Cache the latest kline for a symbol and interval."""
        key = self.KLINE_KEY.format(symbol=symbol, interval=interval)
        self.client.setex(key, ttl, json.dumps(kline, cls=DecimalEncoder))
    
    def get_latest_kline(self, symbol: str, interval: str) -> Optional[Dict[str, Any]]:
        """Get cached latest kline."""
        key = self.KLINE_KEY.format(symbol=symbol, interval=interval)
        data = self.client.get(key)
        
        if data:
            return json.loads(data, object_hook=decimal_decoder)
        return None
    
    def append_kline_to_history(
        self,
        symbol: str,
        interval: str,
        kline: Dict[str, Any],
        max_length: int = 100
    ) -> None:
        """
        Append kline to historical list (Redis list).
        Maintains a rolling window of klines for analysis.
        """
        key = f'klines:{symbol}:{interval}'
        
        # Add to list
        self.client.lpush(key, json.dumps(kline, cls=DecimalEncoder))
        
        # Trim to max length
        self.client.ltrim(key, 0, max_length - 1)
    
    def get_kline_history(
        self,
        symbol: str,
        interval: str,
        count: int = 20
    ) -> List[Dict[str, Any]]:
        """Get historical klines from cache."""
        key = f'klines:{symbol}:{interval}'
        data = self.client.lrange(key, 0, count - 1)
        
        return [json.loads(item, object_hook=decimal_decoder) for item in data]
    
    # =========================================================================
    # EMA CACHING
    # =========================================================================
    
    def set_ema(
        self,
        symbol: str,
        period: int,
        value: Decimal,
        ttl: int = 60
    ) -> None:
        """Cache EMA value."""
        key = self.EMA_KEY.format(symbol=symbol, period=period)
        data = {
            'value': str(value),
            'timestamp': self._get_timestamp()
        }
        self.client.setex(key, ttl, json.dumps(data))
    
    def get_ema(self, symbol: str, period: int) -> Optional[Decimal]:
        """Get cached EMA value."""
        key = self.EMA_KEY.format(symbol=symbol, period=period)
        data = self.client.get(key)
        
        if data:
            parsed = json.loads(data)
            return Decimal(parsed['value'])
        return None
    
    # =========================================================================
    # SIGNAL CACHING
    # =========================================================================
    
    def set_signal(
        self,
        symbol: str,
        signal: Dict[str, Any],
        ttl: int = 300
    ) -> None:
        """Cache a trading signal."""
        key = self.SIGNAL_KEY.format(symbol=symbol)
        signal['timestamp'] = self._get_timestamp()
        self.client.setex(key, ttl, json.dumps(signal, cls=DecimalEncoder))
    
    def get_signal(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get cached trading signal."""
        key = self.SIGNAL_KEY.format(symbol=symbol)
        data = self.client.get(key)
        
        if data:
            return json.loads(data, object_hook=decimal_decoder)
        return None
    
    def clear_signal(self, symbol: str) -> None:
        """Clear trading signal (after execution)."""
        key = self.SIGNAL_KEY.format(symbol=symbol)
        self.client.delete(key)
    
    # =========================================================================
    # SYSTEM STATUS
    # =========================================================================
    
    def set_system_status(self, status: str, reason: str = '') -> None:
        """Set system trading status (ACTIVE, PAUSED, etc.)."""
        data = {
            'status': status,
            'reason': reason,
            'timestamp': self._get_timestamp()
        }
        self.client.set(self.SYSTEM_STATUS_KEY, json.dumps(data))
    
    def get_system_status(self) -> Dict[str, Any]:
        """Get current system status."""
        data = self.client.get(self.SYSTEM_STATUS_KEY)
        
        if data:
            return json.loads(data)
        return {'status': 'UNKNOWN', 'reason': '', 'timestamp': 0}
    
    def is_trading_active(self) -> bool:
        """Check if trading is currently active."""
        status = self.get_system_status()
        return status.get('status') == 'ACTIVE'
    
    # =========================================================================
    # PUBSUB FOR REAL-TIME UPDATES
    # =========================================================================
    
    def publish(self, channel: str, message: Dict[str, Any]) -> None:
        """Publish message to a Redis channel."""
        self.client.publish(channel, json.dumps(message, cls=DecimalEncoder))
    
    def subscribe(self, channel: str):
        """Subscribe to a Redis channel (returns pubsub object)."""
        pubsub = self.client.pubsub()
        pubsub.subscribe(channel)
        return pubsub
    
    # =========================================================================
    # UTILITY
    # =========================================================================
    
    def _get_timestamp(self) -> int:
        """Get current timestamp in milliseconds."""
        import time
        return int(time.time() * 1000)
    
    def flush_symbol(self, symbol: str) -> None:
        """Clear all cached data for a symbol."""
        patterns = [
            self.PRICE_KEY.format(symbol=symbol),
            self.ORDER_BOOK_KEY.format(symbol=symbol),
            f'klines:{symbol}:*',
            f'ema:{symbol}:*',
            self.SIGNAL_KEY.format(symbol=symbol),
        ]
        
        for pattern in patterns:
            if '*' in pattern:
                keys = self.client.keys(pattern)
                if keys:
                    self.client.delete(*keys)
            else:
                self.client.delete(pattern)
    
    def health_check(self) -> bool:
        """Check Redis connection health."""
        try:
            return self.client.ping()
        except:
            return False

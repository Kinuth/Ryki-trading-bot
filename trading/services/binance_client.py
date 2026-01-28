"""
Binance API Client wrapper.
Handles all interaction with Binance REST API.
Uses python-binance with built-in rate limiting and HMAC signing.
"""
import logging
from decimal import Decimal
from typing import Optional, Dict, List, Any
from django.conf import settings
from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceRequestException

logger = logging.getLogger('trading')


class BinanceClient:
    """
    Wrapper for Binance API operations.
    Supports both mainnet and testnet.
    """
    
    # Testnet endpoints
    TESTNET_API_URL = 'https://testnet.binance.vision/api'
    TESTNET_WS_URL = 'wss://testnet.binance.vision/ws'
    
    def __init__(self):
        """Initialize Binance client with credentials from settings."""
        self.api_key = settings.BINANCE_API_KEY
        self.api_secret = settings.BINANCE_API_SECRET
        self.testnet = settings.BINANCE_TESTNET
        
        if not self.api_key or not self.api_secret:
            logger.warning("Binance API credentials not configured")
        
        # Initialize client
        self.client = Client(
            api_key=self.api_key,
            api_secret=self.api_secret,
            testnet=self.testnet
        )
        
        # Explicitly set testnet URL (python-binance doesn't always handle this correctly)
        if self.testnet:
            self.client.API_URL = self.TESTNET_API_URL
        
        logger.info(f"BinanceClient initialized (testnet={self.testnet}, url={self.client.API_URL})")
    
    # =========================================================================
    # ACCOUNT METHODS
    # =========================================================================
    
    def get_account_balance(self, asset: str = 'USDT') -> Decimal:
        """
        Get account balance for a specific asset.
        
        Args:
            asset: Asset symbol (default: USDT)
            
        Returns:
            Available balance as Decimal
        """
        try:
            account = self.client.get_account()
            for balance in account['balances']:
                if balance['asset'] == asset:
                    return Decimal(balance['free'])
            return Decimal('0')
        except (BinanceAPIException, BinanceRequestException) as e:
            logger.error(f"Error getting account balance: {e}")
            raise
    
    def get_account_info(self) -> Dict[str, Any]:
        """Get full account information including all balances."""
        try:
            return self.client.get_account()
        except (BinanceAPIException, BinanceRequestException) as e:
            logger.error(f"Error getting account info: {e}")
            raise
    
    # =========================================================================
    # MARKET DATA METHODS
    # =========================================================================
    
    def get_ticker_price(self, symbol: str) -> Decimal:
        """Get current price for a symbol."""
        try:
            ticker = self.client.get_symbol_ticker(symbol=symbol)
            return Decimal(ticker['price'])
        except (BinanceAPIException, BinanceRequestException) as e:
            logger.error(f"Error getting ticker price for {symbol}: {e}")
            raise
    
    def get_order_book_depth(self, symbol: str, limit: int = 20) -> Dict[str, Any]:
        """
        Get order book depth for slippage analysis.
        
        Args:
            symbol: Trading pair symbol
            limit: Number of price levels (5, 10, 20, 50, 100, 500, 1000, 5000)
            
        Returns:
            Dict with 'bids' and 'asks' lists
        """
        try:
            depth = self.client.get_order_book(symbol=symbol, limit=limit)
            return {
                'bids': [(Decimal(price), Decimal(qty)) for price, qty in depth['bids']],
                'asks': [(Decimal(price), Decimal(qty)) for price, qty in depth['asks']],
                'lastUpdateId': depth['lastUpdateId']
            }
        except (BinanceAPIException, BinanceRequestException) as e:
            logger.error(f"Error getting order book for {symbol}: {e}")
            raise
    
    def get_klines(self, symbol: str, interval: str, limit: int = 100) -> List[Dict]:
        """
        Get candlestick/kline data.
        
        Args:
            symbol: Trading pair symbol
            interval: Kline interval (1m, 5m, 15m, 1h, 4h, 1d, etc.)
            limit: Number of candles to fetch
            
        Returns:
            List of kline data dicts
        """
        try:
            klines = self.client.get_klines(symbol=symbol, interval=interval, limit=limit)
            return [
                {
                    'open_time': kline[0],
                    'open': Decimal(kline[1]),
                    'high': Decimal(kline[2]),
                    'low': Decimal(kline[3]),
                    'close': Decimal(kline[4]),
                    'volume': Decimal(kline[5]),
                    'close_time': kline[6],
                    'quote_volume': Decimal(kline[7]),
                    'trade_count': kline[8],
                }
                for kline in klines
            ]
        except (BinanceAPIException, BinanceRequestException) as e:
            logger.error(f"Error getting klines for {symbol}: {e}")
            raise
    
    def get_24h_ticker(self, symbol: str) -> Dict[str, Any]:
        """Get 24-hour price change statistics."""
        try:
            ticker = self.client.get_ticker(symbol=symbol)
            return {
                'price_change': Decimal(ticker['priceChange']),
                'price_change_percent': Decimal(ticker['priceChangePercent']),
                'weighted_avg_price': Decimal(ticker['weightedAvgPrice']),
                'last_price': Decimal(ticker['lastPrice']),
                'volume': Decimal(ticker['volume']),
                'quote_volume': Decimal(ticker['quoteVolume']),
                'high_price': Decimal(ticker['highPrice']),
                'low_price': Decimal(ticker['lowPrice']),
            }
        except (BinanceAPIException, BinanceRequestException) as e:
            logger.error(f"Error getting 24h ticker for {symbol}: {e}")
            raise
    
    # =========================================================================
    # ORDER METHODS
    # =========================================================================
    
    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        order_type: str = 'LIMIT',
        price: Optional[Decimal] = None,
        time_in_force: str = 'GTC',
        stop_price: Optional[Decimal] = None,
    ) -> Dict[str, Any]:
        """
        Place an order on Binance.
        
        Args:
            symbol: Trading pair symbol
            side: 'BUY' or 'SELL'
            quantity: Order quantity
            order_type: LIMIT, MARKET, STOP_LOSS_LIMIT, TAKE_PROFIT_LIMIT
            price: Limit price (required for LIMIT orders)
            time_in_force: GTC (Good Till Cancel), IOC, FOK
            stop_price: Stop price for stop orders
            
        Returns:
            Order response from Binance
        """
        try:
            params = {
                'symbol': symbol,
                'side': side,
                'type': order_type,
                'quantity': str(quantity),
            }
            
            if order_type == 'LIMIT' or order_type.endswith('_LIMIT'):
                if price is None:
                    raise ValueError("Price required for LIMIT orders")
                params['price'] = str(price)
                params['timeInForce'] = time_in_force
            
            if stop_price:
                params['stopPrice'] = str(stop_price)
            
            order = self.client.create_order(**params)
            
            logger.info(
                f"Order placed: {side} {quantity} {symbol} @ {price or 'MARKET'} "
                f"(orderId={order['orderId']})"
            )
            
            return order
            
        except (BinanceAPIException, BinanceRequestException) as e:
            logger.error(f"Error placing order: {e}")
            raise
    
    def place_market_order(self, symbol: str, side: str, quantity: Decimal) -> Dict[str, Any]:
        """Place a market order."""
        return self.place_order(symbol, side, quantity, order_type='MARKET')
    
    def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        price: Decimal,
        time_in_force: str = 'GTC'
    ) -> Dict[str, Any]:
        """Place a limit order."""
        return self.place_order(
            symbol, side, quantity,
            order_type='LIMIT',
            price=price,
            time_in_force=time_in_force
        )
    
    def cancel_order(self, symbol: str, order_id: int) -> Dict[str, Any]:
        """Cancel a specific order."""
        try:
            result = self.client.cancel_order(symbol=symbol, orderId=order_id)
            logger.info(f"Order cancelled: {symbol} orderId={order_id}")
            return result
        except (BinanceAPIException, BinanceRequestException) as e:
            logger.error(f"Error cancelling order {order_id}: {e}")
            raise
    
    def cancel_all_orders(self, symbol: str) -> List[Dict[str, Any]]:
        """Cancel all open orders for a symbol."""
        try:
            result = self.client.cancel_open_orders(symbol=symbol)
            logger.info(f"Cancelled all orders for {symbol}: {len(result)} orders")
            return result
        except (BinanceAPIException, BinanceRequestException) as e:
            logger.error(f"Error cancelling all orders for {symbol}: {e}")
            raise
    
    def get_order(self, symbol: str, order_id: int) -> Dict[str, Any]:
        """Get order status."""
        try:
            return self.client.get_order(symbol=symbol, orderId=order_id)
        except (BinanceAPIException, BinanceRequestException) as e:
            logger.error(f"Error getting order {order_id}: {e}")
            raise
    
    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all open orders, optionally filtered by symbol."""
        try:
            if symbol:
                return self.client.get_open_orders(symbol=symbol)
            return self.client.get_open_orders()
        except (BinanceAPIException, BinanceRequestException) as e:
            logger.error(f"Error getting open orders: {e}")
            raise
    
    # =========================================================================
    # SYMBOL INFO
    # =========================================================================
    
    def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """Get trading rules and precision for a symbol."""
        try:
            info = self.client.get_symbol_info(symbol)
            
            # Extract relevant filters
            lot_size = next(
                (f for f in info['filters'] if f['filterType'] == 'LOT_SIZE'),
                None
            )
            price_filter = next(
                (f for f in info['filters'] if f['filterType'] == 'PRICE_FILTER'),
                None
            )
            min_notional = next(
                (f for f in info['filters'] if f['filterType'] == 'MIN_NOTIONAL'),
                None
            )
            
            return {
                'symbol': symbol,
                'status': info['status'],
                'base_asset': info['baseAsset'],
                'quote_asset': info['quoteAsset'],
                'base_precision': info['baseAssetPrecision'],
                'quote_precision': info['quoteAssetPrecision'],
                'min_qty': Decimal(lot_size['minQty']) if lot_size else None,
                'max_qty': Decimal(lot_size['maxQty']) if lot_size else None,
                'step_size': Decimal(lot_size['stepSize']) if lot_size else None,
                'min_price': Decimal(price_filter['minPrice']) if price_filter else None,
                'max_price': Decimal(price_filter['maxPrice']) if price_filter else None,
                'tick_size': Decimal(price_filter['tickSize']) if price_filter else None,
                'min_notional': Decimal(min_notional['minNotional']) if min_notional else None,
            }
        except (BinanceAPIException, BinanceRequestException) as e:
            logger.error(f"Error getting symbol info for {symbol}: {e}")
            raise
    
    def format_quantity(self, symbol: str, quantity: Decimal) -> Decimal:
        """Format quantity to meet symbol's step size requirements."""
        info = self.get_symbol_info(symbol)
        step_size = info.get('step_size')
        
        if step_size:
            # Round down to step size
            precision = abs(step_size.as_tuple().exponent)
            return Decimal(str(quantity)).quantize(Decimal(10) ** -precision)
        
        return quantity
    
    def format_price(self, symbol: str, price: Decimal) -> Decimal:
        """Format price to meet symbol's tick size requirements."""
        info = self.get_symbol_info(symbol)
        tick_size = info.get('tick_size')
        
        if tick_size:
            # Round to tick size
            precision = abs(tick_size.as_tuple().exponent)
            return Decimal(str(price)).quantize(Decimal(10) ** -precision)
        
        return price
    
    # =========================================================================
    # UTILITY METHODS
    # =========================================================================
    
    def calculate_slippage_estimate(
        self,
        symbol: str,
        side: str,
        quantity: Decimal
    ) -> Dict[str, Decimal]:
        """
        Estimate slippage for a given order size using order book.
        
        Returns:
            Dict with estimated execution price, slippage amount, and slippage percentage
        """
        order_book = self.get_order_book_depth(symbol, limit=100)
        
        # Use asks for BUY, bids for SELL
        levels = order_book['asks'] if side == 'BUY' else order_book['bids']
        
        remaining_qty = quantity
        total_cost = Decimal('0')
        
        for price, qty in levels:
            if remaining_qty <= 0:
                break
            
            fill_qty = min(remaining_qty, qty)
            total_cost += fill_qty * price
            remaining_qty -= fill_qty
        
        if remaining_qty > 0:
            # Not enough liquidity
            logger.warning(f"Insufficient liquidity for {quantity} {symbol}")
            return {
                'avg_price': Decimal('0'),
                'slippage': Decimal('0'),
                'slippage_pct': Decimal('100'),
                'sufficient_liquidity': False,
            }
        
        avg_price = total_cost / quantity
        best_price = levels[0][0]
        slippage = abs(avg_price - best_price)
        slippage_pct = (slippage / best_price) * 100
        
        return {
            'avg_price': avg_price,
            'slippage': slippage,
            'slippage_pct': slippage_pct,
            'sufficient_liquidity': True,
        }

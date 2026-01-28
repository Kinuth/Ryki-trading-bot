# Ryki Algorithmic Trading Bot

Production-grade, event-driven algorithmic trading system integrating Binance API with Volume Price Analysis (VPA) and Three-Dimensional Approach trading strategies.

## Features

- **VPA Pattern Recognition**: Climax bars, No Demand/Supply, Stopping Volume, Tests
- **3D Analysis**: Relational (cross-market), Fundamental (CPI/PPI), Technical (multi-timeframe)
- **Risk Management**: Position sizing, slippage protection, trailing stops, circuit breaker
- **Real-time Streaming**: Binance WebSocket for klines and order book
- **Live Dashboard**: WebSocket-based real-time updates

## Trading Pairs

BTCUSDT, ETHUSDT, BNBUSDT, XRPUSDT, SOLUSDT

## Tech Stack

- Django 5.x + Django REST Framework
- Django Channels (WebSockets)
- Celery + Redis (task queue)
- PostgreSQL (trade logs)
- python-binance SDK

## Quick Start

### 1. Clone and Setup

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy environment file
cp .env.example .env
```

### 2. Configure Environment

Edit `.env` with your Binance API credentials:

```env
BINANCE_API_KEY=your_api_key
BINANCE_API_SECRET=your_api_secret
BINANCE_TESTNET=True  # Use testnet for testing
```

### 3. Start Services

**Using Docker (recommended):**

```bash
docker-compose up -d
```

**Manual setup:**

```bash
# Terminal 1: Redis
redis-server

# Terminal 2: PostgreSQL (ensure running)

# Terminal 3: Run migrations
python manage.py migrate

# Terminal 4: Start Django
python manage.py runserver 8000

# Terminal 5: Start Celery worker
celery -A ryki_trading worker -l info

# Terminal 6: Start Celery beat
celery -A ryki_trading beat -l info

# Terminal 7: Start WebSocket streamer
python manage.py run_websocket
```

### 4. Initialize Trading System

```bash
python manage.py init_trading
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/trades/` | GET | List all trades |
| `/api/positions/` | GET | List open positions |
| `/api/positions/{id}/close/` | POST | Close a position |
| `/api/risk/today/` | GET | Today's risk metrics |
| `/api/system/status/` | GET | System status |
| `/api/system/pause/` | POST | Pause trading |
| `/api/system/resume/` | POST | Resume trading |
| `/api/prices/` | GET | Current prices |
| `/api/trade/` | POST | Manual trade |

## WebSocket Endpoints

| Endpoint | Description |
|----------|-------------|
| `ws://host/ws/dashboard/` | Live dashboard updates |
| `ws://host/ws/prices/` | Real-time price stream |

## Strategy Logic

Signals are generated when ALL conditions are met:

1. **VPA Valid Pattern** - Climax, No Demand/Supply, or other VPA pattern detected
2. **3D Confluence** - At least 2/3 dimensions aligned (Relational + Fundamental + Technical)
3. **EMA Deviation** - Price deviates from 20-period EMA by threshold
4. **Post-Macro Event** - Within trading window after CPI/PPI release

## Risk Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| Account Risk | 1.5% | Max risk per trade |
| Max Slippage | 0.2% | Order book slippage limit |
| Trailing Trigger | 2% | Profit to activate trailing stop |
| Daily Drawdown | 5% | Circuit breaker trigger |

## Partial Fill Handling

When a LIMIT order receives a partial fill:

1. Trade status updates to `PARTIALLY_FILLED`
2. Order monitoring continues (2-second intervals)
3. Dashboard receives real-time updates
4. Position is created only when fully filled
5. If cancelled before fill, remaining quantity released

## License

MIT
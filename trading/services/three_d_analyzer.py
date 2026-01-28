"""
Three-Dimensional (3D) Approach Analyzer.
Implements Anna Coulling's 3D methodology:
1. Relational Analysis - Cross-market correlations
2. Fundamental Analysis - Macro economic events (CPI/PPI)
3. Technical Analysis - Multi-timeframe trend alignment
"""
import logging
from decimal import Decimal
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass
from enum import Enum
from datetime import datetime, timedelta
import numpy as np
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger('trading')


class DimensionAlignment(Enum):
    """Alignment state for each dimension."""
    BULLISH = 'BULLISH'
    BEARISH = 'BEARISH'
    NEUTRAL = 'NEUTRAL'
    CONFLICTING = 'CONFLICTING'


@dataclass
class RelationalAnalysis:
    """Results from cross-market correlation analysis."""
    btc_eth_correlation: float  # -1 to 1
    crypto_health: DimensionAlignment  # Overall crypto market health
    usd_impact: DimensionAlignment  # Dollar strength impact
    risk_sentiment: str  # RISK_ON or RISK_OFF
    description: str


@dataclass
class FundamentalAnalysis:
    """Results from macro economic analysis."""
    upcoming_events: List[Dict[str, Any]]
    recent_events: List[Dict[str, Any]]
    event_impact: DimensionAlignment
    time_to_next_event: Optional[timedelta]
    post_event_window: bool  # True if within trading window after event
    description: str


@dataclass
class TechnicalAnalysis:
    """Results from multi-timeframe technical analysis."""
    timeframe_trends: Dict[str, DimensionAlignment]  # e.g., {'1m': BULLISH, '5m': BEARISH}
    trend_alignment: float  # 0.0 to 1.0 (1.0 = all timeframes agree)
    primary_trend: DimensionAlignment
    ema_positions: Dict[str, float]  # Price position relative to EMA per timeframe
    description: str


@dataclass
class ThreeDSignal:
    """Combined 3D analysis result."""
    relational: RelationalAnalysis
    fundamental: FundamentalAnalysis
    technical: TechnicalAnalysis
    confluence: DimensionAlignment
    confluence_score: float  # 0.0 to 1.0
    dimensions_aligned: int  # Count of aligned dimensions (0-3)
    is_valid_signal: bool
    description: str


class ThreeDAnalyzer:
    """
    Implements Anna Coulling's Three-Dimensional Approach.
    
    Combines three analytical dimensions:
    1. Relational - What are related markets telling us?
    2. Fundamental - What's the macro backdrop?
    3. Technical - What's the price action saying?
    """
    
    # Trading windows around economic events
    PRE_EVENT_AVOID_MINUTES = 30  # Don't trade 30 min before events
    POST_EVENT_TRADE_MINUTES = 60  # Trading window after events
    
    # Correlation thresholds
    STRONG_CORRELATION = 0.7
    WEAK_CORRELATION = 0.3
    
    # Timeframes for multi-TF analysis
    TIMEFRAMES = ['1m', '5m', '15m', '1h']
    
    def __init__(self, redis_cache=None, binance_client=None):
        """
        Initialize 3D analyzer.
        
        Args:
            redis_cache: RedisCache instance for cached data
            binance_client: BinanceClient for market data
        """
        self.redis_cache = redis_cache
        self.binance_client = binance_client
        self.ema_period = settings.EMA_PERIOD
    
    def analyze(
        self,
        symbol: str,
        klines_by_timeframe: Dict[str, List[Dict[str, Any]]],
        related_prices: Optional[Dict[str, Decimal]] = None,
    ) -> ThreeDSignal:
        """
        Perform full 3D analysis.
        
        Args:
            symbol: Primary trading symbol
            klines_by_timeframe: Dict of timeframe -> klines
            related_prices: Prices of related assets for correlation
            
        Returns:
            ThreeDSignal with complete analysis
        """
        # Analyze each dimension
        relational = self._analyze_relational(symbol, related_prices)
        fundamental = self._analyze_fundamental()
        technical = self._analyze_technical(symbol, klines_by_timeframe)
        
        # Calculate confluence
        confluence, confluence_score, dimensions_aligned = self._calculate_confluence(
            relational, fundamental, technical
        )
        
        # Determine if valid signal
        is_valid = self._is_valid_signal(
            confluence, confluence_score, dimensions_aligned, fundamental
        )
        
        # Generate description
        description = self._generate_description(
            confluence, dimensions_aligned, relational, fundamental, technical
        )
        
        return ThreeDSignal(
            relational=relational,
            fundamental=fundamental,
            technical=technical,
            confluence=confluence,
            confluence_score=confluence_score,
            dimensions_aligned=dimensions_aligned,
            is_valid_signal=is_valid,
            description=description
        )
    
    # =========================================================================
    # RELATIONAL ANALYSIS
    # =========================================================================
    
    def _analyze_relational(
        self,
        symbol: str,
        related_prices: Optional[Dict[str, Decimal]] = None
    ) -> RelationalAnalysis:
        """
        Analyze cross-market relationships.
        
        Examines:
        - BTC/ETH correlation (crypto market health)
        - USD index impact
        - Risk sentiment (risk-on vs risk-off)
        """
        # Default values if no data available
        if not related_prices:
            return RelationalAnalysis(
                btc_eth_correlation=0.0,
                crypto_health=DimensionAlignment.NEUTRAL,
                usd_impact=DimensionAlignment.NEUTRAL,
                risk_sentiment="NEUTRAL",
                description="No relational data available"
            )
        
        btc_eth_corr = 0.0
        crypto_health = DimensionAlignment.NEUTRAL
        usd_impact = DimensionAlignment.NEUTRAL
        risk_sentiment = "NEUTRAL"
        
        # Calculate BTC/ETH correlation if we have price history
        # For now, use price ratio as a proxy
        if 'BTCUSDT' in related_prices and 'ETHUSDT' in related_prices:
            btc_price = float(related_prices['BTCUSDT'])
            eth_price = float(related_prices['ETHUSDT'])
            
            # ETH/BTC ratio - if rising, altcoins are strong (risk-on)
            eth_btc_ratio = eth_price / btc_price if btc_price > 0 else 0
            
            # Historical average ETH/BTC is ~0.05-0.08
            if eth_btc_ratio > 0.06:
                crypto_health = DimensionAlignment.BULLISH
                risk_sentiment = "RISK_ON"
            elif eth_btc_ratio < 0.04:
                crypto_health = DimensionAlignment.BEARISH
                risk_sentiment = "RISK_OFF"
            
            btc_eth_corr = 0.85  # Crypto assets are typically highly correlated
        
        description = (
            f"Crypto market {'healthy' if crypto_health == DimensionAlignment.BULLISH else 'weak' if crypto_health == DimensionAlignment.BEARISH else 'neutral'}, "
            f"Risk sentiment: {risk_sentiment}"
        )
        
        return RelationalAnalysis(
            btc_eth_correlation=btc_eth_corr,
            crypto_health=crypto_health,
            usd_impact=usd_impact,
            risk_sentiment=risk_sentiment,
            description=description
        )
    
    # =========================================================================
    # FUNDAMENTAL ANALYSIS
    # =========================================================================
    
    def _analyze_fundamental(self) -> FundamentalAnalysis:
        """
        Analyze macro economic events.
        
        Looks at:
        - Upcoming CPI/PPI releases
        - Recent event impacts
        - Whether we're in a tradeable post-event window
        """
        from trading.models import EconomicEvent
        
        now = timezone.now()
        
        # Get upcoming events (next 24 hours)
        upcoming = EconomicEvent.objects.filter(
            release_time__gt=now,
            release_time__lt=now + timedelta(hours=24),
            impact__in=['HIGH', 'MEDIUM']
        ).order_by('release_time')[:5]
        
        # Get recent events (last 2 hours)
        recent = EconomicEvent.objects.filter(
            release_time__gt=now - timedelta(hours=2),
            release_time__lt=now,
            impact__in=['HIGH', 'MEDIUM']
        ).order_by('-release_time')[:5]
        
        upcoming_list = list(upcoming.values('event_type', 'release_time', 'impact', 'forecast'))
        recent_list = list(recent.values('event_type', 'release_time', 'actual', 'forecast', 'deviation_from_forecast'))
        
        # Check if we're approaching an event
        time_to_next = None
        if upcoming_list:
            next_event_time = upcoming_list[0]['release_time']
            time_to_next = next_event_time - now
        
        # Check if we're in post-event trading window
        post_event_window = False
        event_impact = DimensionAlignment.NEUTRAL
        
        if recent_list:
            last_event_time = recent_list[0]['release_time']
            time_since_event = now - last_event_time
            
            if time_since_event.total_seconds() < self.POST_EVENT_TRADE_MINUTES * 60:
                post_event_window = True
                
                # Assess impact direction from deviation
                deviation = recent_list[0].get('deviation_from_forecast')
                if deviation:
                    if deviation > 0.5:  # Positive surprise
                        event_impact = DimensionAlignment.BULLISH  # Generally USD bullish = crypto bearish
                    elif deviation < -0.5:  # Negative surprise
                        event_impact = DimensionAlignment.BEARISH
        
        # Generate description
        if time_to_next and time_to_next.total_seconds() < self.PRE_EVENT_AVOID_MINUTES * 60:
            description = f"Caution: High-impact event in {int(time_to_next.total_seconds() / 60)} minutes"
        elif post_event_window:
            description = f"Post-event trading window active, impact: {event_impact.value}"
        else:
            description = "No immediate macro events affecting market"
        
        return FundamentalAnalysis(
            upcoming_events=upcoming_list,
            recent_events=recent_list,
            event_impact=event_impact,
            time_to_next_event=time_to_next,
            post_event_window=post_event_window,
            description=description
        )
    
    # =========================================================================
    # TECHNICAL ANALYSIS
    # =========================================================================
    
    def _analyze_technical(
        self,
        symbol: str,
        klines_by_timeframe: Dict[str, List[Dict[str, Any]]]
    ) -> TechnicalAnalysis:
        """
        Multi-timeframe technical analysis.
        
        Analyzes:
        - Trend direction on each timeframe
        - Price position relative to EMA
        - Trend alignment across timeframes
        """
        timeframe_trends = {}
        ema_positions = {}
        
        for tf, klines in klines_by_timeframe.items():
            if len(klines) < self.ema_period:
                timeframe_trends[tf] = DimensionAlignment.NEUTRAL
                ema_positions[tf] = 0.0
                continue
            
            # Calculate EMA
            closes = [float(k['close']) for k in klines]
            ema = self._calculate_ema(closes, self.ema_period)
            current_price = closes[-1]
            
            # Calculate position relative to EMA
            ema_deviation = (current_price - ema) / ema if ema > 0 else 0
            ema_positions[tf] = ema_deviation
            
            # Determine trend
            if ema_deviation > settings.EMA_DEVIATION_THRESHOLD:
                timeframe_trends[tf] = DimensionAlignment.BULLISH
            elif ema_deviation < -settings.EMA_DEVIATION_THRESHOLD:
                timeframe_trends[tf] = DimensionAlignment.BEARISH
            else:
                timeframe_trends[tf] = DimensionAlignment.NEUTRAL
        
        # Calculate trend alignment
        trend_alignment, primary_trend = self._calculate_trend_alignment(timeframe_trends)
        
        # Generate description
        aligned_count = sum(
            1 for t in timeframe_trends.values() 
            if t == primary_trend and t != DimensionAlignment.NEUTRAL
        )
        description = (
            f"Primary trend: {primary_trend.value}, "
            f"{aligned_count}/{len(timeframe_trends)} timeframes aligned"
        )
        
        return TechnicalAnalysis(
            timeframe_trends=timeframe_trends,
            trend_alignment=trend_alignment,
            primary_trend=primary_trend,
            ema_positions=ema_positions,
            description=description
        )
    
    def _calculate_ema(self, prices: List[float], period: int) -> float:
        """Calculate Exponential Moving Average."""
        if len(prices) < period:
            return np.mean(prices) if prices else 0
        
        multiplier = 2 / (period + 1)
        ema = np.mean(prices[:period])  # SMA for first period
        
        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema
        
        return ema
    
    def _calculate_trend_alignment(
        self,
        timeframe_trends: Dict[str, DimensionAlignment]
    ) -> Tuple[float, DimensionAlignment]:
        """
        Calculate how aligned trends are across timeframes.
        
        Returns:
            (alignment_score, primary_trend)
        """
        if not timeframe_trends:
            return 0.0, DimensionAlignment.NEUTRAL
        
        bullish_count = sum(1 for t in timeframe_trends.values() if t == DimensionAlignment.BULLISH)
        bearish_count = sum(1 for t in timeframe_trends.values() if t == DimensionAlignment.BEARISH)
        total = len(timeframe_trends)
        
        if bullish_count > bearish_count:
            primary_trend = DimensionAlignment.BULLISH
            alignment = bullish_count / total
        elif bearish_count > bullish_count:
            primary_trend = DimensionAlignment.BEARISH
            alignment = bearish_count / total
        else:
            primary_trend = DimensionAlignment.NEUTRAL
            alignment = 0.0
        
        return alignment, primary_trend
    
    # =========================================================================
    # CONFLUENCE CALCULATION
    # =========================================================================
    
    def _calculate_confluence(
        self,
        relational: RelationalAnalysis,
        fundamental: FundamentalAnalysis,
        technical: TechnicalAnalysis
    ) -> Tuple[DimensionAlignment, float, int]:
        """
        Calculate overall confluence from all three dimensions.
        
        Returns:
            (confluence_direction, confluence_score, dimensions_aligned)
        """
        dimensions = []
        
        # Add relational alignment (weighted by crypto health)
        if relational.crypto_health != DimensionAlignment.NEUTRAL:
            dimensions.append(relational.crypto_health)
        
        # Add fundamental alignment (only if in post-event window)
        if fundamental.post_event_window and fundamental.event_impact != DimensionAlignment.NEUTRAL:
            dimensions.append(fundamental.event_impact)
        
        # Add technical alignment (most important)
        if technical.primary_trend != DimensionAlignment.NEUTRAL:
            dimensions.append(technical.primary_trend)
        
        if not dimensions:
            return DimensionAlignment.NEUTRAL, 0.0, 0
        
        bullish_count = sum(1 for d in dimensions if d == DimensionAlignment.BULLISH)
        bearish_count = sum(1 for d in dimensions if d == DimensionAlignment.BEARISH)
        
        if bullish_count >= 2:
            confluence = DimensionAlignment.BULLISH
            dimensions_aligned = bullish_count
        elif bearish_count >= 2:
            confluence = DimensionAlignment.BEARISH
            dimensions_aligned = bearish_count
        elif bullish_count == 1 and bearish_count == 1:
            confluence = DimensionAlignment.CONFLICTING
            dimensions_aligned = 0
        elif bullish_count == 1:
            confluence = DimensionAlignment.BULLISH
            dimensions_aligned = 1
        elif bearish_count == 1:
            confluence = DimensionAlignment.BEARISH
            dimensions_aligned = 1
        else:
            confluence = DimensionAlignment.NEUTRAL
            dimensions_aligned = 0
        
        # Calculate confluence score
        max_possible = len(dimensions)
        confluence_score = dimensions_aligned / max_possible if max_possible > 0 else 0.0
        
        # Boost score if technical alignment is strong
        if technical.trend_alignment >= 0.75:
            confluence_score = min(confluence_score * 1.2, 1.0)
        
        return confluence, confluence_score, dimensions_aligned
    
    def _is_valid_signal(
        self,
        confluence: DimensionAlignment,
        confluence_score: float,
        dimensions_aligned: int,
        fundamental: FundamentalAnalysis
    ) -> bool:
        """
        Determine if the 3D analysis produces a valid trading signal.
        
        Criteria:
        - At least 2 dimensions must align
        - Must not be approaching a high-impact event
        - Confluence score >= 0.6
        """
        # Check for conflicting signals
        if confluence == DimensionAlignment.CONFLICTING:
            return False
        
        if confluence == DimensionAlignment.NEUTRAL:
            return False
        
        # Need at least 2 dimensions aligned
        if dimensions_aligned < 2:
            return False
        
        # Avoid trading before high-impact events
        if fundamental.time_to_next_event:
            minutes_to_event = fundamental.time_to_next_event.total_seconds() / 60
            if minutes_to_event < self.PRE_EVENT_AVOID_MINUTES:
                return False
        
        # Require minimum confluence score
        if confluence_score < 0.6:
            return False
        
        return True
    
    def _generate_description(
        self,
        confluence: DimensionAlignment,
        dimensions_aligned: int,
        relational: RelationalAnalysis,
        fundamental: FundamentalAnalysis,
        technical: TechnicalAnalysis
    ) -> str:
        """Generate comprehensive description of 3D analysis."""
        
        parts = [f"3D Confluence: {confluence.value}"]
        parts.append(f"Dimensions aligned: {dimensions_aligned}/3")
        parts.append(f"Relational: {relational.crypto_health.value}")
        
        if fundamental.post_event_window:
            parts.append(f"Fundamental: Post-event {fundamental.event_impact.value}")
        
        parts.append(f"Technical: {technical.primary_trend.value} ({technical.trend_alignment:.0%} aligned)")
        
        return " | ".join(parts)

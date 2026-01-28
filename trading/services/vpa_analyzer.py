"""
Volume Price Analysis (VPA) Analyzer.
Implements Anna Coulling's VPA methodology for candlestick analysis.
Identifies key patterns: Climax, No Demand, No Supply, Stopping Volume, Test bars.
"""
import logging
from decimal import Decimal
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from enum import Enum
import numpy as np
from django.conf import settings

logger = logging.getLogger('trading')


class VPAPattern(Enum):
    """VPA candlestick patterns."""
    CLIMAX_HIGH = 'CLIMAX_HIGH'       # Ultra high volume, wide spread - potential reversal
    CLIMAX_LOW = 'CLIMAX_LOW'         # Ultra high volume selling climax
    NO_DEMAND = 'NO_DEMAND'           # Low volume up bar - bearish
    NO_SUPPLY = 'NO_SUPPLY'           # Low volume down bar - bullish
    STOPPING_VOLUME = 'STOPPING_VOLUME'  # High volume, narrow spread - absorption
    TEST = 'TEST'                     # Low volume test of support/resistance
    UPTHRUST = 'UPTHRUST'             # False breakout up
    SPRING = 'SPRING'                 # False breakout down (bullish)
    EFFORT_VS_RESULT = 'EFFORT_VS_RESULT'  # High volume but little movement
    NEUTRAL = 'NEUTRAL'               # No significant pattern


class TrendDirection(Enum):
    """Market trend direction."""
    BULLISH = 'BULLISH'
    BEARISH = 'BEARISH'
    NEUTRAL = 'NEUTRAL'


@dataclass
class VPASignal:
    """VPA analysis result."""
    pattern: VPAPattern
    direction: TrendDirection
    strength: float  # 0.0 to 1.0
    description: str
    volume_anomaly: float  # Z-score of volume
    spread_ratio: float  # Current spread vs average
    close_position: float  # 0.0 (low) to 1.0 (high) within bar
    is_valid_signal: bool


class VPAAnalyzer:
    """
    Implements Anna Coulling's Volume Price Analysis methodology.
    
    Core principles:
    1. Volume validates price movement
    2. Spread (range) shows effort
    3. Close position shows outcome
    4. Context determines significance
    """
    
    # Volume thresholds (standard deviations from mean)
    ULTRA_HIGH_VOLUME = 2.5
    HIGH_VOLUME = 1.5
    LOW_VOLUME = -0.5
    ULTRA_LOW_VOLUME = -1.5
    
    # Spread thresholds (as ratio to average)
    WIDE_SPREAD = 1.5
    NARROW_SPREAD = 0.5
    
    # Close position thresholds (0-1 range)
    UPPER_THIRD = 0.67
    LOWER_THIRD = 0.33
    
    def __init__(self, lookback_period: int = 20):
        """
        Initialize VPA analyzer.
        
        Args:
            lookback_period: Number of bars for calculating averages
        """
        self.lookback_period = lookback_period
        self.volume_threshold = settings.VOLUME_ANOMALY_THRESHOLD
    
    def analyze(self, candles: List[Dict[str, Any]]) -> VPASignal:
        """
        Analyze a series of candles and identify VPA patterns.
        
        Args:
            candles: List of candle dicts with OHLCV data
                    (most recent last)
                    
        Returns:
            VPASignal with identified pattern and metrics
        """
        if len(candles) < self.lookback_period:
            return VPASignal(
                pattern=VPAPattern.NEUTRAL,
                direction=TrendDirection.NEUTRAL,
                strength=0.0,
                description="Insufficient data for VPA analysis",
                volume_anomaly=0.0,
                spread_ratio=1.0,
                close_position=0.5,
                is_valid_signal=False
            )
        
        # Extract current candle and historical data
        current = candles[-1]
        historical = candles[:-1][-self.lookback_period:]
        
        # Calculate metrics
        volume_anomaly = self._calculate_volume_anomaly(current, historical)
        spread_ratio = self._calculate_spread_ratio(current, historical)
        close_position = self._calculate_close_position(current)
        
        # Determine if current candle is bullish or bearish
        is_bullish = Decimal(str(current['close'])) >= Decimal(str(current['open']))
        
        # Detect trend from recent price action
        trend = self._detect_trend(historical)
        
        # Identify pattern
        pattern = self._identify_pattern(
            volume_anomaly=volume_anomaly,
            spread_ratio=spread_ratio,
            close_position=close_position,
            is_bullish=is_bullish,
            trend=trend
        )
        
        # Calculate signal strength
        strength = self._calculate_strength(pattern, volume_anomaly, spread_ratio)
        
        # Determine signal direction
        direction = self._get_signal_direction(pattern, trend)
        
        # Generate description
        description = self._generate_description(
            pattern, volume_anomaly, spread_ratio, close_position, is_bullish
        )
        
        # Determine if this is a valid trading signal
        is_valid = self._is_valid_signal(pattern, strength, trend)
        
        return VPASignal(
            pattern=pattern,
            direction=direction,
            strength=strength,
            description=description,
            volume_anomaly=volume_anomaly,
            spread_ratio=spread_ratio,
            close_position=close_position,
            is_valid_signal=is_valid
        )
    
    def _calculate_volume_anomaly(
        self,
        current: Dict[str, Any],
        historical: List[Dict[str, Any]]
    ) -> float:
        """
        Calculate volume z-score compared to historical average.
        
        Returns:
            Z-score (positive = above average, negative = below)
        """
        volumes = [float(c['volume']) for c in historical]
        current_volume = float(current['volume'])
        
        if len(volumes) < 2:
            return 0.0
        
        mean = np.mean(volumes)
        std = np.std(volumes)
        
        if std == 0:
            return 0.0
        
        return (current_volume - mean) / std
    
    def _calculate_spread_ratio(
        self,
        current: Dict[str, Any],
        historical: List[Dict[str, Any]]
    ) -> float:
        """
        Calculate current spread as ratio to average spread.
        
        Returns:
            Ratio (1.0 = average, >1 = wide, <1 = narrow)
        """
        spreads = [float(c['high']) - float(c['low']) for c in historical]
        current_spread = float(current['high']) - float(current['low'])
        
        avg_spread = np.mean(spreads) if spreads else current_spread
        
        if avg_spread == 0:
            return 1.0
        
        return current_spread / avg_spread
    
    def _calculate_close_position(self, candle: Dict[str, Any]) -> float:
        """
        Calculate where price closed within the bar's range.
        
        Returns:
            0.0 = closed at low, 1.0 = closed at high
        """
        high = float(candle['high'])
        low = float(candle['low'])
        close = float(candle['close'])
        
        spread = high - low
        if spread == 0:
            return 0.5
        
        return (close - low) / spread
    
    def _detect_trend(self, candles: List[Dict[str, Any]]) -> TrendDirection:
        """Detect short-term trend from recent price action."""
        if len(candles) < 5:
            return TrendDirection.NEUTRAL
        
        # Use closes of last 5 candles
        closes = [float(c['close']) for c in candles[-5:]]
        
        # Simple linear regression slope
        x = np.arange(len(closes))
        slope = np.polyfit(x, closes, 1)[0]
        
        # Normalize slope by average price
        avg_price = np.mean(closes)
        normalized_slope = (slope / avg_price) * 100  # As percentage
        
        if normalized_slope > 0.05:
            return TrendDirection.BULLISH
        elif normalized_slope < -0.05:
            return TrendDirection.BEARISH
        else:
            return TrendDirection.NEUTRAL
    
    def _identify_pattern(
        self,
        volume_anomaly: float,
        spread_ratio: float,
        close_position: float,
        is_bullish: bool,
        trend: TrendDirection
    ) -> VPAPattern:
        """Identify VPA pattern from metrics."""
        
        # CLIMAX BARS - Ultra high volume, wide spread
        if volume_anomaly >= self.ULTRA_HIGH_VOLUME and spread_ratio >= self.WIDE_SPREAD:
            if is_bullish and trend == TrendDirection.BULLISH:
                return VPAPattern.CLIMAX_HIGH  # Potential top
            elif not is_bullish and trend == TrendDirection.BEARISH:
                return VPAPattern.CLIMAX_LOW  # Potential bottom
        
        # STOPPING VOLUME - High volume but narrow spread (absorption)
        if volume_anomaly >= self.HIGH_VOLUME and spread_ratio <= self.NARROW_SPREAD:
            return VPAPattern.STOPPING_VOLUME
        
        # EFFORT VS RESULT - High volume but minimal price movement
        if volume_anomaly >= self.HIGH_VOLUME and spread_ratio < 0.75:
            return VPAPattern.EFFORT_VS_RESULT
        
        # NO DEMAND - Low volume up bar, especially in uptrend
        if (volume_anomaly <= self.LOW_VOLUME and 
            is_bullish and 
            close_position >= self.UPPER_THIRD):
            return VPAPattern.NO_DEMAND
        
        # NO SUPPLY - Low volume down bar, especially in downtrend
        if (volume_anomaly <= self.LOW_VOLUME and 
            not is_bullish and 
            close_position <= self.LOWER_THIRD):
            return VPAPattern.NO_SUPPLY
        
        # TEST - Low volume testing support/resistance
        if volume_anomaly <= self.ULTRA_LOW_VOLUME:
            return VPAPattern.TEST
        
        # UPTHRUST - Wide spread up, closes weak (lower third)
        if (spread_ratio >= self.WIDE_SPREAD and 
            is_bullish and 
            close_position <= self.LOWER_THIRD and
            volume_anomaly >= 0):
            return VPAPattern.UPTHRUST
        
        # SPRING - Wide spread down, closes strong (upper third)
        if (spread_ratio >= self.WIDE_SPREAD and 
            not is_bullish and 
            close_position >= self.UPPER_THIRD and
            volume_anomaly >= 0):
            return VPAPattern.SPRING
        
        return VPAPattern.NEUTRAL
    
    def _calculate_strength(
        self,
        pattern: VPAPattern,
        volume_anomaly: float,
        spread_ratio: float
    ) -> float:
        """
        Calculate signal strength (0.0 to 1.0).
        
        Higher volume anomalies and clearer patterns = stronger signals.
        """
        if pattern == VPAPattern.NEUTRAL:
            return 0.0
        
        # Base strength from pattern type
        pattern_weights = {
            VPAPattern.CLIMAX_HIGH: 0.9,
            VPAPattern.CLIMAX_LOW: 0.9,
            VPAPattern.STOPPING_VOLUME: 0.8,
            VPAPattern.UPTHRUST: 0.85,
            VPAPattern.SPRING: 0.85,
            VPAPattern.NO_DEMAND: 0.7,
            VPAPattern.NO_SUPPLY: 0.7,
            VPAPattern.TEST: 0.6,
            VPAPattern.EFFORT_VS_RESULT: 0.65,
            VPAPattern.NEUTRAL: 0.0,
        }
        
        base_strength = pattern_weights.get(pattern, 0.5)
        
        # Adjust by volume significance
        volume_factor = min(abs(volume_anomaly) / 3.0, 1.0)
        
        # Final strength
        strength = base_strength * (0.7 + 0.3 * volume_factor)
        
        return min(max(strength, 0.0), 1.0)
    
    def _get_signal_direction(
        self,
        pattern: VPAPattern,
        trend: TrendDirection
    ) -> TrendDirection:
        """Determine trading direction from pattern."""
        
        # Bullish patterns
        bullish_patterns = [
            VPAPattern.CLIMAX_LOW,  # Selling exhaustion
            VPAPattern.NO_SUPPLY,   # No selling pressure
            VPAPattern.SPRING,      # Failed breakdown
        ]
        
        # Bearish patterns
        bearish_patterns = [
            VPAPattern.CLIMAX_HIGH,  # Buying exhaustion
            VPAPattern.NO_DEMAND,    # No buying pressure
            VPAPattern.UPTHRUST,     # Failed breakout
        ]
        
        # Neutral/confirmation patterns (follow trend)
        if pattern == VPAPattern.STOPPING_VOLUME:
            # Stopping volume often precedes reversal
            return (TrendDirection.BEARISH if trend == TrendDirection.BULLISH 
                    else TrendDirection.BULLISH)
        
        if pattern == VPAPattern.TEST:
            # Successful test confirms trend
            return trend
        
        if pattern in bullish_patterns:
            return TrendDirection.BULLISH
        elif pattern in bearish_patterns:
            return TrendDirection.BEARISH
        
        return TrendDirection.NEUTRAL
    
    def _generate_description(
        self,
        pattern: VPAPattern,
        volume_anomaly: float,
        spread_ratio: float,
        close_position: float,
        is_bullish: bool
    ) -> str:
        """Generate human-readable description of the analysis."""
        
        vol_desc = "ultra high" if volume_anomaly >= 2.5 else \
                   "high" if volume_anomaly >= 1.5 else \
                   "average" if volume_anomaly >= -0.5 else \
                   "low" if volume_anomaly >= -1.5 else "very low"
        
        spread_desc = "wide" if spread_ratio >= 1.5 else \
                      "narrow" if spread_ratio <= 0.5 else "average"
        
        close_desc = "upper" if close_position >= 0.67 else \
                     "lower" if close_position <= 0.33 else "middle"
        
        pattern_descriptions = {
            VPAPattern.CLIMAX_HIGH: f"Buying climax detected - {vol_desc} volume with {spread_desc} spread, potential reversal",
            VPAPattern.CLIMAX_LOW: f"Selling climax detected - {vol_desc} volume with {spread_desc} spread, potential bottom",
            VPAPattern.NO_DEMAND: f"No Demand - {vol_desc} volume up bar closing in {close_desc} third, weak buying",
            VPAPattern.NO_SUPPLY: f"No Supply - {vol_desc} volume down bar, selling drying up",
            VPAPattern.STOPPING_VOLUME: f"Stopping Volume - {vol_desc} volume absorbed with {spread_desc} spread",
            VPAPattern.TEST: f"Test bar - {vol_desc} volume testing price level",
            VPAPattern.UPTHRUST: f"Upthrust - {spread_desc} spread up bar closing weak, bearish",
            VPAPattern.SPRING: f"Spring - {spread_desc} spread down bar closing strong, bullish",
            VPAPattern.EFFORT_VS_RESULT: f"Effort vs Result mismatch - {vol_desc} volume but minimal movement",
            VPAPattern.NEUTRAL: "No significant VPA pattern detected",
        }
        
        return pattern_descriptions.get(pattern, "Unknown pattern")
    
    def _is_valid_signal(
        self,
        pattern: VPAPattern,
        strength: float,
        trend: TrendDirection
    ) -> bool:
        """
        Determine if this is a valid trading signal.
        
        Criteria:
        - Pattern must not be NEUTRAL
        - Strength must be above threshold
        - Pattern must align with or oppose trend appropriately
        """
        if pattern == VPAPattern.NEUTRAL:
            return False
        
        if strength < 0.5:
            return False
        
        # Reversal patterns are valid when opposing trend
        reversal_patterns = [
            VPAPattern.CLIMAX_HIGH, VPAPattern.CLIMAX_LOW,
            VPAPattern.UPTHRUST, VPAPattern.SPRING,
            VPAPattern.STOPPING_VOLUME
        ]
        
        if pattern in reversal_patterns:
            return True
        
        # Continuation patterns need trend alignment
        if pattern == VPAPattern.NO_DEMAND and trend != TrendDirection.BULLISH:
            return True  # Valid bearish signal when not in strong uptrend
        
        if pattern == VPAPattern.NO_SUPPLY and trend != TrendDirection.BEARISH:
            return True  # Valid bullish signal when not in strong downtrend
        
        return True
    
    def get_volume_profile(
        self,
        candles: List[Dict[str, Any]],
        num_bins: int = 10
    ) -> Dict[str, Any]:
        """
        Calculate volume profile for price levels.
        Useful for identifying support/resistance.
        """
        if not candles:
            return {}
        
        prices = []
        volumes = []
        
        for c in candles:
            # Use typical price (HLC/3)
            typical_price = (float(c['high']) + float(c['low']) + float(c['close'])) / 3
            prices.append(typical_price)
            volumes.append(float(c['volume']))
        
        # Create price bins
        min_price = min(prices)
        max_price = max(prices)
        bin_size = (max_price - min_price) / num_bins if max_price > min_price else 1
        
        profile = {}
        for price, volume in zip(prices, volumes):
            bin_idx = int((price - min_price) / bin_size) if bin_size > 0 else 0
            bin_idx = min(bin_idx, num_bins - 1)
            bin_price = min_price + (bin_idx + 0.5) * bin_size
            
            if bin_price not in profile:
                profile[bin_price] = 0
            profile[bin_price] += volume
        
        # Find POC (Point of Control) - price level with highest volume
        poc = max(profile, key=profile.get) if profile else 0
        
        return {
            'profile': profile,
            'poc': poc,
            'value_area_high': max_price,
            'value_area_low': min_price,
        }

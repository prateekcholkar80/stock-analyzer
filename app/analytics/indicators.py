from collections.abc import Callable

import numpy as np
import talib

from app.exceptions import (
    IndicatorCalculationError,
    InsufficientDataError,
)
from app.models.market import HistoricalCandleSeries
from app.models.technical import (
    IndicatorBundle,
    IndicatorComponent,
    IndicatorPoint,
    IndicatorSeries,
    PriceField,
)


IndicatorCalculator = Callable[..., np.ndarray]


def calculate_sma(
    series: HistoricalCandleSeries,
    period: int,
    price_field: PriceField = PriceField.CLOSE,
) -> IndicatorSeries:
    """Calculate a simple moving average from validated candle data."""
    return _calculate_moving_average(
        series=series,
        period=period,
        price_field=price_field,
        indicator="SMA",
        calculator=talib.SMA,
    )


def calculate_ema(
    series: HistoricalCandleSeries,
    period: int,
    price_field: PriceField = PriceField.CLOSE,
) -> IndicatorSeries:
    """Calculate an exponential moving average from candle data."""
    return _calculate_moving_average(
        series=series,
        period=period,
        price_field=price_field,
        indicator="EMA",
        calculator=talib.EMA,
    )


def calculate_rsi(
    series: HistoricalCandleSeries,
    period: int = 14,
    price_field: PriceField = PriceField.CLOSE,
) -> IndicatorSeries:
    """Calculate Wilder's relative strength index from candle data."""
    _validate_period("RSI", period)

    return _calculate_single_price_indicator(
        series=series,
        period=period,
        price_field=price_field,
        indicator="RSI",
        calculator=talib.RSI,
        minimum_candles=period + 1,
        first_valid_index=period,
        expected_range=(0.0, 100.0),
    )


def calculate_macd(
    series: HistoricalCandleSeries,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
    price_field: PriceField = PriceField.CLOSE,
) -> IndicatorBundle:
    """Calculate MACD, signal, and histogram components."""
    _validate_period("MACD fast", fast_period)
    _validate_period("MACD slow", slow_period)
    _validate_period("MACD signal", signal_period)

    if fast_period >= slow_period:
        raise ValueError(
            "MACD fast period must be less than slow period"
        )

    first_valid_index = slow_period + signal_period - 2
    minimum_candles = first_valid_index + 1
    candle_count = len(series.candles)
    if candle_count < minimum_candles:
        raise InsufficientDataError(
            f"MACD({fast_period}, {slow_period}, {signal_period}) "
            f"requires at least {minimum_candles} candles; "
            f"received {candle_count}"
        )

    prices = np.asarray(
        [
            getattr(candle, price_field.value)
            for candle in series.candles
        ],
        dtype=np.float64,
    )

    try:
        raw_result = talib.MACD(
            prices,
            fastperiod=fast_period,
            slowperiod=slow_period,
            signalperiod=signal_period,
        )
    except Exception as error:
        raise IndicatorCalculationError(
            "TA-Lib could not calculate MACD"
        ) from error

    if not isinstance(raw_result, tuple) or len(raw_result) != 3:
        raise IndicatorCalculationError(
            "TA-Lib did not return three MACD output arrays"
        )

    try:
        calculated_components = tuple(
            np.asarray(values, dtype=np.float64)
            for values in raw_result
        )
    except Exception as error:
        raise IndicatorCalculationError(
            "TA-Lib returned invalid MACD output arrays"
        ) from error

    component_names = ("macd", "signal", "histogram")
    valid_components: list[np.ndarray] = []
    for name, calculated in zip(
        component_names,
        calculated_components,
    ):
        if calculated.ndim != 1 or calculated.size != candle_count:
            raise IndicatorCalculationError(
                f"TA-Lib returned an invalid MACD {name} result shape"
            )

        valid_values = calculated[first_valid_index:]
        if not np.all(np.isfinite(valid_values)):
            raise IndicatorCalculationError(
                f"TA-Lib returned non-finite MACD {name} values"
            )

        valid_components.append(valid_values)

    macd_values, signal_values, histogram_values = valid_components
    if not np.allclose(
        histogram_values,
        macd_values - signal_values,
        rtol=1e-9,
        atol=1e-12,
    ):
        raise IndicatorCalculationError(
            "TA-Lib returned an inconsistent MACD histogram"
        )

    candles = series.candles[first_valid_index:]
    components = [
        IndicatorComponent(
            name=name,
            points=[
                IndicatorPoint(
                    timestamp=candle.timestamp,
                    value=float(value),
                )
                for candle, value in zip(candles, values)
            ],
        )
        for name, values in zip(component_names, valid_components)
    ]

    return IndicatorBundle(
        exchange=series.exchange,
        symbol_token=series.symbol_token,
        symbol=series.symbol,
        interval=series.interval,
        indicator="MACD",
        price_field=price_field,
        parameters={
            "fast_period": fast_period,
            "slow_period": slow_period,
            "signal_period": signal_period,
        },
        components=components,
    )


def calculate_bollinger_bands(
    series: HistoricalCandleSeries,
    period: int = 20,
    upper_deviation: float = 2.0,
    lower_deviation: float = 2.0,
    price_field: PriceField = PriceField.CLOSE,
) -> IndicatorBundle:
    """Calculate standard SMA-based Bollinger Bands."""
    _validate_period("Bollinger Bands", period)
    normalized_upper_deviation = _validate_positive_number(
        "Bollinger Bands upper deviation",
        upper_deviation,
    )
    normalized_lower_deviation = _validate_positive_number(
        "Bollinger Bands lower deviation",
        lower_deviation,
    )

    candle_count = len(series.candles)
    if candle_count < period:
        raise InsufficientDataError(
            f"Bollinger Bands({period}) requires at least {period} "
            f"candles; received {candle_count}"
        )

    prices = np.asarray(
        [
            getattr(candle, price_field.value)
            for candle in series.candles
        ],
        dtype=np.float64,
    )

    try:
        raw_result = talib.BBANDS(
            prices,
            timeperiod=period,
            nbdevup=normalized_upper_deviation,
            nbdevdn=normalized_lower_deviation,
            matype=0,
        )
    except Exception as error:
        raise IndicatorCalculationError(
            "TA-Lib could not calculate Bollinger Bands"
        ) from error

    if not isinstance(raw_result, tuple) or len(raw_result) != 3:
        raise IndicatorCalculationError(
            "TA-Lib did not return three Bollinger Bands output arrays"
        )

    try:
        calculated_components = tuple(
            np.asarray(values, dtype=np.float64)
            for values in raw_result
        )
    except Exception as error:
        raise IndicatorCalculationError(
            "TA-Lib returned invalid Bollinger Bands output arrays"
        ) from error

    first_valid_index = period - 1
    component_names = (
        "upper_band",
        "middle_band",
        "lower_band",
    )
    valid_components: list[np.ndarray] = []
    for name, calculated in zip(
        component_names,
        calculated_components,
    ):
        if calculated.ndim != 1 or calculated.size != candle_count:
            raise IndicatorCalculationError(
                "TA-Lib returned an invalid Bollinger Bands "
                f"{name} result shape"
            )

        valid_values = calculated[first_valid_index:]
        if not np.all(np.isfinite(valid_values)):
            raise IndicatorCalculationError(
                "TA-Lib returned non-finite Bollinger Bands "
                f"{name} values"
            )

        valid_components.append(valid_values)

    upper_values, middle_values, lower_values = valid_components
    if np.any(upper_values < middle_values) or np.any(
        middle_values < lower_values
    ):
        raise IndicatorCalculationError(
            "TA-Lib returned incorrectly ordered Bollinger Bands"
        )

    candles = series.candles[first_valid_index:]
    components = [
        IndicatorComponent(
            name=name,
            points=[
                IndicatorPoint(
                    timestamp=candle.timestamp,
                    value=float(value),
                )
                for candle, value in zip(candles, values)
            ],
        )
        for name, values in zip(component_names, valid_components)
    ]

    return IndicatorBundle(
        exchange=series.exchange,
        symbol_token=series.symbol_token,
        symbol=series.symbol,
        interval=series.interval,
        indicator="BBANDS",
        price_field=price_field,
        parameters={
            "period": period,
            "upper_deviation": normalized_upper_deviation,
            "lower_deviation": normalized_lower_deviation,
            "moving_average_type": "SMA",
        },
        components=components,
    )


def calculate_atr(
    series: HistoricalCandleSeries,
    period: int = 14,
) -> IndicatorSeries:
    """Calculate Wilder's Average True Range from HLC candle data."""
    _validate_period("ATR", period)

    minimum_candles = period + 1
    candle_count = len(series.candles)
    if candle_count < minimum_candles:
        raise InsufficientDataError(
            f"ATR({period}) requires at least {minimum_candles} "
            f"candles; received {candle_count}"
        )

    highs = np.asarray(
        [candle.high for candle in series.candles],
        dtype=np.float64,
    )
    lows = np.asarray(
        [candle.low for candle in series.candles],
        dtype=np.float64,
    )
    closes = np.asarray(
        [candle.close for candle in series.candles],
        dtype=np.float64,
    )

    try:
        calculated = np.asarray(
            talib.ATR(
                highs,
                lows,
                closes,
                timeperiod=period,
            ),
            dtype=np.float64,
        )
    except Exception as error:
        raise IndicatorCalculationError(
            "TA-Lib could not calculate ATR"
        ) from error

    if calculated.ndim != 1 or calculated.size != candle_count:
        raise IndicatorCalculationError(
            "TA-Lib returned an invalid ATR result shape"
        )

    valid_values = calculated[period:]
    if not np.all(np.isfinite(valid_values)):
        raise IndicatorCalculationError(
            "TA-Lib returned non-finite ATR values"
        )

    if np.any(valid_values < 0):
        raise IndicatorCalculationError(
            "TA-Lib returned negative ATR values"
        )

    points = [
        IndicatorPoint(
            timestamp=candle.timestamp,
            value=float(value),
        )
        for candle, value in zip(
            series.candles[period:],
            valid_values,
        )
    ]

    return IndicatorSeries(
        exchange=series.exchange,
        symbol_token=series.symbol_token,
        symbol=series.symbol,
        interval=series.interval,
        indicator="ATR",
        input_fields=(
            PriceField.HIGH,
            PriceField.LOW,
            PriceField.CLOSE,
        ),
        parameters={"period": period},
        points=points,
    )


def calculate_adx(
    series: HistoricalCandleSeries,
    period: int = 14,
) -> IndicatorBundle:
    """Calculate ADX and directional indicators from HLC data."""
    _validate_period("ADX", period)

    first_valid_index = 2 * period - 1
    minimum_candles = first_valid_index + 1
    candle_count = len(series.candles)
    if candle_count < minimum_candles:
        raise InsufficientDataError(
            f"ADX({period}) requires at least {minimum_candles} "
            f"candles; received {candle_count}"
        )

    highs = np.asarray(
        [candle.high for candle in series.candles],
        dtype=np.float64,
    )
    lows = np.asarray(
        [candle.low for candle in series.candles],
        dtype=np.float64,
    )
    closes = np.asarray(
        [candle.close for candle in series.candles],
        dtype=np.float64,
    )

    try:
        calculated_components = (
            np.asarray(
                talib.ADX(
                    highs,
                    lows,
                    closes,
                    timeperiod=period,
                ),
                dtype=np.float64,
            ),
            np.asarray(
                talib.PLUS_DI(
                    highs,
                    lows,
                    closes,
                    timeperiod=period,
                ),
                dtype=np.float64,
            ),
            np.asarray(
                talib.MINUS_DI(
                    highs,
                    lows,
                    closes,
                    timeperiod=period,
                ),
                dtype=np.float64,
            ),
        )
    except Exception as error:
        raise IndicatorCalculationError(
            "TA-Lib could not calculate ADX"
        ) from error

    component_names = ("adx", "plus_di", "minus_di")
    valid_components: list[np.ndarray] = []
    for name, calculated in zip(
        component_names,
        calculated_components,
    ):
        if calculated.ndim != 1 or calculated.size != candle_count:
            raise IndicatorCalculationError(
                f"TA-Lib returned an invalid ADX {name} result shape"
            )

        valid_values = calculated[first_valid_index:]
        if not np.all(np.isfinite(valid_values)):
            raise IndicatorCalculationError(
                f"TA-Lib returned non-finite ADX {name} values"
            )

        if np.any((valid_values < 0) | (valid_values > 100)):
            raise IndicatorCalculationError(
                f"TA-Lib returned ADX {name} values outside the "
                "expected range 0.0 to 100.0"
            )

        valid_components.append(valid_values)

    candles = series.candles[first_valid_index:]
    components = [
        IndicatorComponent(
            name=name,
            points=[
                IndicatorPoint(
                    timestamp=candle.timestamp,
                    value=float(value),
                )
                for candle, value in zip(candles, values)
            ],
        )
        for name, values in zip(component_names, valid_components)
    ]

    return IndicatorBundle(
        exchange=series.exchange,
        symbol_token=series.symbol_token,
        symbol=series.symbol,
        interval=series.interval,
        indicator="ADX",
        input_fields=(
            PriceField.HIGH,
            PriceField.LOW,
            PriceField.CLOSE,
        ),
        parameters={"period": period},
        components=components,
    )


def calculate_stochastic(
    series: HistoricalCandleSeries,
    fast_k_period: int = 14,
    slow_k_period: int = 3,
    slow_d_period: int = 3,
) -> IndicatorBundle:
    """Calculate the slow Stochastic Oscillator from HLC data."""
    _validate_period("Stochastic fast K", fast_k_period)
    _validate_period("Stochastic slow K", slow_k_period)
    _validate_period("Stochastic slow D", slow_d_period)

    first_valid_index = (
        fast_k_period + slow_k_period + slow_d_period - 3
    )
    minimum_candles = first_valid_index + 1
    candle_count = len(series.candles)
    if candle_count < minimum_candles:
        raise InsufficientDataError(
            "Stochastic"
            f"({fast_k_period}, {slow_k_period}, {slow_d_period}) "
            f"requires at least {minimum_candles} candles; "
            f"received {candle_count}"
        )

    highs = np.asarray(
        [candle.high for candle in series.candles],
        dtype=np.float64,
    )
    lows = np.asarray(
        [candle.low for candle in series.candles],
        dtype=np.float64,
    )
    closes = np.asarray(
        [candle.close for candle in series.candles],
        dtype=np.float64,
    )

    try:
        raw_result = talib.STOCH(
            highs,
            lows,
            closes,
            fastk_period=fast_k_period,
            slowk_period=slow_k_period,
            slowk_matype=0,
            slowd_period=slow_d_period,
            slowd_matype=0,
        )
    except Exception as error:
        raise IndicatorCalculationError(
            "TA-Lib could not calculate Stochastic"
        ) from error

    if not isinstance(raw_result, tuple) or len(raw_result) != 2:
        raise IndicatorCalculationError(
            "TA-Lib did not return two Stochastic output arrays"
        )

    try:
        calculated_components = tuple(
            np.asarray(values, dtype=np.float64)
            for values in raw_result
        )
    except Exception as error:
        raise IndicatorCalculationError(
            "TA-Lib returned invalid Stochastic output arrays"
        ) from error

    component_names = ("percent_k", "percent_d")
    valid_components: list[np.ndarray] = []
    for name, calculated in zip(
        component_names,
        calculated_components,
    ):
        if calculated.ndim != 1 or calculated.size != candle_count:
            raise IndicatorCalculationError(
                "TA-Lib returned an invalid Stochastic "
                f"{name} result shape"
            )

        valid_values = calculated[first_valid_index:]
        if not np.all(np.isfinite(valid_values)):
            raise IndicatorCalculationError(
                f"TA-Lib returned non-finite Stochastic {name} values"
            )

        if np.any((valid_values < 0) | (valid_values > 100)):
            raise IndicatorCalculationError(
                f"TA-Lib returned Stochastic {name} values outside "
                "the expected range 0.0 to 100.0"
            )

        valid_components.append(valid_values)

    candles = series.candles[first_valid_index:]
    components = [
        IndicatorComponent(
            name=name,
            points=[
                IndicatorPoint(
                    timestamp=candle.timestamp,
                    value=float(value),
                )
                for candle, value in zip(candles, values)
            ],
        )
        for name, values in zip(component_names, valid_components)
    ]

    return IndicatorBundle(
        exchange=series.exchange,
        symbol_token=series.symbol_token,
        symbol=series.symbol,
        interval=series.interval,
        indicator="STOCH",
        input_fields=(
            PriceField.HIGH,
            PriceField.LOW,
            PriceField.CLOSE,
        ),
        parameters={
            "fast_k_period": fast_k_period,
            "slow_k_period": slow_k_period,
            "slow_d_period": slow_d_period,
            "moving_average_type": "SMA",
        },
        components=components,
    )


def calculate_obv(
    series: HistoricalCandleSeries,
    price_field: PriceField = PriceField.CLOSE,
) -> IndicatorSeries:
    """Calculate On-Balance Volume from price direction and volume."""
    if price_field is PriceField.VOLUME:
        raise ValueError("OBV price field must be an OHLC field")

    candle_count = len(series.candles)
    if candle_count < 1:
        raise InsufficientDataError(
            "OBV requires at least 1 candle; received 0"
        )

    prices = np.asarray(
        [
            getattr(candle, price_field.value)
            for candle in series.candles
        ],
        dtype=np.float64,
    )
    volumes = np.asarray(
        [candle.volume for candle in series.candles],
        dtype=np.float64,
    )

    try:
        calculated = np.asarray(
            talib.OBV(prices, volumes),
            dtype=np.float64,
        )
    except Exception as error:
        raise IndicatorCalculationError(
            "TA-Lib could not calculate OBV"
        ) from error

    if calculated.ndim != 1 or calculated.size != candle_count:
        raise IndicatorCalculationError(
            "TA-Lib returned an invalid OBV result shape"
        )

    if not np.all(np.isfinite(calculated)):
        raise IndicatorCalculationError(
            "TA-Lib returned non-finite OBV values"
        )

    points = [
        IndicatorPoint(
            timestamp=candle.timestamp,
            value=float(value),
        )
        for candle, value in zip(series.candles, calculated)
    ]

    return IndicatorSeries(
        exchange=series.exchange,
        symbol_token=series.symbol_token,
        symbol=series.symbol,
        interval=series.interval,
        indicator="OBV",
        input_fields=(price_field, PriceField.VOLUME),
        parameters={},
        points=points,
    )


def _calculate_moving_average(
    *,
    series: HistoricalCandleSeries,
    period: int,
    price_field: PriceField,
    indicator: str,
    calculator: IndicatorCalculator,
) -> IndicatorSeries:
    _validate_period(indicator, period)

    return _calculate_single_price_indicator(
        series=series,
        period=period,
        price_field=price_field,
        indicator=indicator,
        calculator=calculator,
        minimum_candles=period,
        first_valid_index=period - 1,
    )


def _calculate_single_price_indicator(
    *,
    series: HistoricalCandleSeries,
    period: int,
    price_field: PriceField,
    indicator: str,
    calculator: IndicatorCalculator,
    minimum_candles: int,
    first_valid_index: int,
    expected_range: tuple[float, float] | None = None,
) -> IndicatorSeries:
    _validate_period(indicator, period)

    candle_count = len(series.candles)
    if candle_count < minimum_candles:
        raise InsufficientDataError(
            f"{indicator}({period}) requires at least "
            f"{minimum_candles} candles; received {candle_count}"
        )

    prices = np.asarray(
        [
            getattr(candle, price_field.value)
            for candle in series.candles
        ],
        dtype=np.float64,
    )

    try:
        calculated = np.asarray(
            calculator(prices, timeperiod=period),
            dtype=np.float64,
        )
    except Exception as error:
        raise IndicatorCalculationError(
            f"TA-Lib could not calculate {indicator}"
        ) from error

    if calculated.ndim != 1 or calculated.size != candle_count:
        raise IndicatorCalculationError(
            f"TA-Lib returned an invalid {indicator} result shape"
        )

    valid_values = calculated[first_valid_index:]
    if not np.all(np.isfinite(valid_values)):
        raise IndicatorCalculationError(
            f"TA-Lib returned non-finite {indicator} values"
        )

    if expected_range is not None:
        minimum_value, maximum_value = expected_range
        if np.any(
            (valid_values < minimum_value)
            | (valid_values > maximum_value)
        ):
            raise IndicatorCalculationError(
                f"TA-Lib returned {indicator} values outside the "
                f"expected range {minimum_value} to {maximum_value}"
            )

    points = [
        IndicatorPoint(
            timestamp=candle.timestamp,
            value=float(value),
        )
        for candle, value in zip(
            series.candles[first_valid_index:],
            valid_values,
        )
    ]

    return IndicatorSeries(
        exchange=series.exchange,
        symbol_token=series.symbol_token,
        symbol=series.symbol,
        interval=series.interval,
        indicator=indicator,
        price_field=price_field,
        parameters={"period": period},
        points=points,
    )


def _validate_period(indicator: str, period: int) -> None:
    if isinstance(period, bool) or not isinstance(period, int):
        raise ValueError(f"{indicator} period must be an integer")

    if period < 2:
        raise ValueError(f"{indicator} period must be at least 2")


def _validate_positive_number(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")

    normalized_value = float(value)
    if not np.isfinite(normalized_value):
        raise ValueError(f"{name} must be finite")

    if normalized_value <= 0:
        raise ValueError(f"{name} must be greater than zero")

    return normalized_value

"""Causal signal generation: input consists of CLOSED candles only.

Liquidity sweeps are price-action proxies, never identified whale orders.
The NDS levels are exposed as a research reference, not silently changed.
"""
from dataclasses import dataclass, asdict
from math import isfinite
from statistics import mean


@dataclass(frozen=True)
class Bar:
    time: float
    open: float
    high: float
    low: float
    close: float
    volume: float = 0


def valid_bars(bars):
    return all(
        all(isfinite(x) for x in (b.time, b.open, b.high, b.low, b.close))
        and b.low > 0 and b.low <= min(b.open, b.close) <= max(b.open, b.close) <= b.high
        and (i == 0 or b.time > bars[i - 1].time)
        for i, b in enumerate(bars)
    )


def ema(values, period):
    value = values[0]
    for item in values[1:]:
        value += 2 / (period + 1) * (item - value)
    return value


def atr(bars, period=14):
    if len(bars) <= period:
        return 0
    return mean(max(b.high - b.low, abs(b.high - p.close), abs(b.low - p.close))
                for p, b in zip(bars[-period - 1:-1], bars[-period:]))


def nds_reference(bars):
    """HA cycle with original .864 / 1.272 / .65 levels; no execution."""
    data = bars[-101:]
    if len(data) < 5:
        return None
    ho = hc = 0
    color = run = 0
    start = None
    direction = 0
    for i, b in enumerate(data):
        ho = (b.open + b.close) / 2 if i == 0 else (ho + hc) / 2
        hc = (b.open + b.high + b.low + b.close) / 4
        current = 1 if hc > ho else -1
        run = run + 1 if current == color else 1
        color = current
        if run == 3:
            start, direction = i - 2, color
    if start is None or start == 0:
        return None
    p0 = data[start - 1].low if direction == 1 else data[start - 1].high
    p1 = max(b.high for b in data[start:]) if direction == 1 else min(b.low for b in data[start:])
    size = abs(p1 - p0)
    return dict(direction="BUY" if direction == 1 else "SELL", point0=p0, point1=p1,
                entry=p1 - direction * size * .864,
                sl=p1 - direction * size * 1.272,
                tp=p1 - direction * size * .65,
                reward_risk=(.864 - .65) / (1.272 - .864))


def analyze(symbol, m1, m5, config):
    result = dict(symbol=symbol, direction="WAIT", setup="liquidity-trend-v1",
                  reason="در انتظار ۱۲۰ کندل M1 و ۶۰ کندل M5 بسته‌شده", checks=[], candidate=None)
    if len(m1) < 120 or len(m5) < 60 or not valid_bars(m1) or not valid_bars(m5):
        return result
    a = atr(m1)
    if a <= 0:
        return result
    closes = [b.close for b in m5]
    fast, slow = ema(closes, 20), ema(closes, 50)
    previous_fast = ema(closes[:-3], 20)
    up = fast > slow and closes[-1] > slow and fast > previous_fast
    down = fast < slow and closes[-1] < slow and fast < previous_fast
    trend = 1 if up else -1 if down else 0
    # Sweep on penultimate bar; confirmation on latest closed bar.
    history, sweep, confirm = m1[-22:-2], m1[-2], m1[-1]
    low, high = min(b.low for b in history), max(b.high for b in history)
    bullish_sweep = sweep.low < low and sweep.close > low
    bearish_sweep = sweep.high > high and sweep.close < high
    buy_confirm = confirm.close > sweep.high and confirm.close > confirm.open
    sell_confirm = confirm.close < sweep.low and confirm.close < confirm.open
    swept = bullish_sweep if trend == 1 else bearish_sweep if trend == -1 else False
    confirmed = buy_confirm if trend == 1 else sell_confirm if trend == -1 else False
    body_ok = abs(confirm.close - confirm.open) >= .2 * a
    checks = [dict(id="trend", label="روند و شیب EMA20/50 در M5", ok=trend != 0),
              dict(id="sweep", label="شکار نقدینگی و بازگشت به محدوده در M1", ok=swept),
              dict(id="structure", label="شکست ساختار در کندل بسته‌شدهٔ بعدی", ok=confirmed),
              dict(id="body", label="قدرت تأیید حداقل ۰٫۲ ATR", ok=body_ok)]
    result.update(atr=a, ema20=fast, ema50=slow,
                  trend="صعودی" if up else "نزولی" if down else "خنثی",
                  checks=checks, nds=nds_reference(m1),
                  bars=[asdict(b) for b in m1[-80:]], bar_time=confirm.time)
    failed = next((c for c in checks if not c['ok']), None)
    if failed:
        result['reason'] = failed['label'] + " تأیید نشده"
        return result
    direction = "BUY" if trend == 1 else "SELL"
    sl = sweep.low - .15 * a if trend == 1 else sweep.high + .15 * a
    result.update(direction=direction, reason="ستاپ تأیید شد؛ بررسی ریسک و هزینهٔ ورود")
    result['candidate'] = dict(symbol=symbol, direction=direction, sl=sl,
                               signal_time=confirm.time + 60,
                               cycle_id=f"{symbol}:{int(sweep.time)}:{direction}",
                               atr=a, features={"symbol": symbol, "setup": "sweep",
                                                "trend": "up" if up else "down"})
    return result

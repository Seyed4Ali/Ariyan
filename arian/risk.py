"""Risk caps use equity and ROUND DOWN volume; never round up to broker minimum."""
from datetime import datetime, timezone
from math import floor, isfinite


def session_key(ts):
    hour = datetime.fromtimestamp(ts, timezone.utc).hour
    return "london" if hour < 12 else "overlap" if hour < 16 else "late"


def trade_window(ts, config, symbol=None):
    t = datetime.fromtimestamp(ts, timezone.utc)
    hour = t.hour
    custom = config.get('market_sessions',{}).get(symbol)
    if custom:
        return (t.weekday() in custom['days'] and custom['start'] <= hour < custom['end']
                and (not custom.get('rollover',True) or not config['rollover_start_utc'] <= hour < config['rollover_end_utc']))
    return (t.weekday() < 5 and config['session_start_utc'] <= hour < config['session_end_utc']
            and not config['rollover_start_utc'] <= hour < config['rollover_end_utc'])


def size_for_risk(equity, risk_percent, loss_per_lot, commission_per_lot,
                  volume_min, volume_max, volume_step):
    values = [equity, risk_percent, loss_per_lot, commission_per_lot, volume_min, volume_max, volume_step]
    if not all(isfinite(v) for v in values) or min(equity, risk_percent, loss_per_lot, volume_min, volume_step) <= 0:
        return 0
    if commission_per_lot < 0:
        return 0
    budget = equity * risk_percent / 100
    raw = min(volume_max, budget / (loss_per_lot + commission_per_lot))
    lots = floor((raw + 1e-12) / volume_step) * volume_step
    if lots < volume_min - 1e-10 or lots * (loss_per_lot + commission_per_lot) > budget + 1e-7:
        return 0
    return round(lots, 8)


def entry_checks(now, tick, candidate, config, metrics, news, paused, learned):
    age = now - tick['time']
    bar_age = now - candidate['signal_time']
    spread = tick['ask'] - tick['bid']
    return [
        dict(id="feed", label="قیمت زنده و کندل تازه", ok=0 <= age <= config['max_tick_age_seconds'] and 0 <= bar_age <= 75),
        dict(id="session", label="بازهٔ ورود بیت‌کوین؛ مشروط به خوراک تازهٔ بروکر" if candidate['symbol']=='BTCUSD' else
             "بازهٔ معاملاتی ۰۷–۱۷ UTC؛ روز کاری", ok=trade_window(now, config, candidate['symbol'])),
        dict(id="spread", label="اسپرد حداکثر ۱۲٪ ATR", ok=0 <= spread <= candidate['atr'] * config['max_spread_atr']),
        dict(id="news", label="تقویم اخبار بررسی‌شده و خارج از محدودهٔ خبر", ok=news['ok']),
        dict(id="daily", label="افت امروز کمتر از سقف روزانه", ok=metrics['daily_drawdown_percent'] < config['daily_loss_percent']),
        dict(id="drawdown", label="افت سرمایه کمتر از سقف کل", ok=metrics['drawdown_percent'] < config['max_drawdown_percent']),
        dict(id="streak", label="زیان متوالی کمتر از حد توقف", ok=metrics['consecutive_losses'] < config['max_consecutive_losses']),
        dict(id="count", label="تعداد ورود امروز کمتر از سقف", ok=metrics['trades_today'] < config['max_trades_day']),
        dict(id="position", label="حداکثر یک پوزیشن بین همهٔ نمادها", ok=metrics['open_count'] == 0),
        dict(id="cooldown", label="فاصلهٔ پنج دقیقه از آخرین خروج", ok=now - metrics['last_exit'] >= config['cooldown_seconds']),
        dict(id="pause", label="اجازهٔ ورود فعال است", ok=not paused),
        dict(id="learned", label="چک‌لیست یادگرفته‌شدهٔ نسخهٔ جاری", ok=learned),
    ]

"""Short-lived isolated MT5 IPC worker. No live-account execution code path."""
import json
import math
import sys
import time
from datetime import datetime, timezone
from .clock import broker_to_utc, broker_offset
from .timeframes import TIMEFRAMES, price_bar


def run(request):
    import MetaTrader5 as mt5
    if not mt5.initialize(path=request['terminal_path'], timeout=5000):
        return dict(connected=False, error=f"MT5: {mt5.last_error()}")
    try:
        ti, ai = mt5.terminal_info(), mt5.account_info()
        if not ti or not ti.connected or not ai:
            return dict(connected=False, error="متاتریدر باز است؛ به حساب بروکر وارد شوید")
        identity = f"{ai.server}:{ai.login}"
        clock = request.get('broker_clock', {'mode':'utc'})
        if clock.get('mode') == 'fxpro-eu' and not ai.server.startswith('FxPro-'):
            raise ValueError('پروفایل ساعت FxPro با بروکر جاری سازگار نیست؛ تنظیمات ساعت را اصلاح کنید')
        status = dict(connected=True, server=ai.server, currency=ai.currency,
                      demo=ai.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO,
                      identity=identity, equity=ai.equity, balance=ai.balance,
                      account_login=ai.login, profit=getattr(ai,'profit',0),
                      margin_free=getattr(ai,'margin_free',ai.equity),
                      trade_allowed=bool(ti.trade_allowed and ai.trade_allowed and not ti.tradeapi_disabled),
                      broker_offset_seconds=broker_offset(time.time(),clock))
        action = request.get('action', 'snapshot')
        if action != 'snapshot':
            # Account switching, including demo -> real, cannot carry an armed session.
            if not status['demo'] or request.get('identity') != identity:
                raise ValueError('ارسال سفارش فقط روی همان حساب دمو مجاز است')
            if not status['trade_allowed'] or ai.currency != 'USD':
                raise ValueError('Algo Trading غیرفعال است یا ارز حساب USD نیست')
            return send_demo(mt5, request, status)
        markets = {}
        for canonical, symbol in request['symbols'].items():
            if not mt5.symbol_select(symbol, True):
                markets[canonical] = dict(error=f"نماد {symbol} در بروکر یافت نشد؛ نام را در تنظیمات اصلاح کنید")
                continue
            info, tick = mt5.symbol_info(symbol), mt5.symbol_info_tick(symbol)
            if not info or not tick or tick.bid <= 0 or tick.ask < tick.bid:
                markets[canonical] = dict(error='قیمت معتبر در دسترس نیست'); continue
            frames = {}
            for name in TIMEFRAMES:
                tf = getattr(mt5, 'TIMEFRAME_' + name)
                current = mt5.copy_rates_from_pos(symbol, tf, 0, 1)
                frame = dict(current=price_bar(current[-1],clock) if current is not None and len(current) else None)
                if request.get('include_history', True):
                    # Trading analysis always excludes the in-progress candle.
                    rates = mt5.copy_rates_from_pos(symbol, tf, 1, 220)
                    frame['bars'] = [] if rates is None else [price_bar(r,clock) for r in rates]
                frames[name] = frame
            step = info.trade_tick_size
            p = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, symbol, 1., tick.ask, tick.ask + step) if step > 0 else None
            factor = p / step if p is not None and p > 0 else 0
            markets[canonical] = dict(timeframes=frames,
                tick=dict(time=broker_to_utc(tick.time_msc / 1000,clock), bid=tick.bid, ask=tick.ask),
                spec=dict(symbol=symbol, point=info.point, digits=info.digits, tick_size=step,
                          volume_min=info.volume_min, volume_max=info.volume_max, volume_step=info.volume_step,
                          stops_level=info.trade_stops_level, factor=factor, trade_mode=info.trade_mode))
        positions, orders = mt5.positions_get(), mt5.orders_get()
        if positions is None or orders is None:
            raise ValueError('دریافت وضعیت پوزیشن/سفارش ناموفق؛ ورود مسدود')
        status['positions'] = [p._asdict() for p in positions]
        status['orders_count'] = len(orders)
        since = request.get('history_since', time.time() - 30 * 86400)
        deals = mt5.history_deals_get(datetime.fromtimestamp(since-86400, timezone.utc),
                                     datetime.fromtimestamp(time.time()+86400,timezone.utc))
        status['deals'] = None if deals is None else [dict(d._asdict(),
            time=broker_to_utc(d.time,clock), time_msc=broker_to_utc(d.time_msc/1000,clock)*1000)
            for d in deals if d.magic == 20261030]
        return dict(**status, markets=markets, fetched=time.time())
    finally:
        mt5.shutdown()


def send_demo(mt5, request, status):
    """Only a fully specified engine instruction, same demo identity, market deals with SL/TP."""
    command = request['command']
    symbol = command['broker_symbol']
    info, tick = mt5.symbol_info(symbol), mt5.symbol_info_tick(symbol)
    if not info or not tick or not 0 <= time.time() - broker_to_utc(tick.time_msc / 1000,request.get('broker_clock',{'mode':'utc'})) <= 15:
        raise ValueError('قیمت معتبر و تازه نیست')
    positions, orders = mt5.positions_get(), mt5.orders_get()
    if positions is None or orders is None:
        raise ValueError('وضعیت بروکر نامشخص است')
    closing = request['action'] == 'demo_close'
    if closing:
        found = [p for p in positions if p.ticket == command['ticket'] and p.magic == 20261030]
        if len(found) != 1:
            raise ValueError('پوزیشن متعلق به این ربات نیست یا قبلاً بسته شده')
        p = found[0]
        side = mt5.ORDER_TYPE_SELL if p.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
        volume = p.volume
    else:
        if positions or orders:
            raise ValueError('حساب دمو باید فقط در اختیار ربات و بدون پوزیشن/سفارش دیگر باشد')
        side = mt5.ORDER_TYPE_BUY if command['direction'] == 'BUY' else mt5.ORDER_TYPE_SELL
        volume = float(command['volume'])
    price = tick.ask if side == mt5.ORDER_TYPE_BUY else tick.bid
    order = dict(action=mt5.TRADE_ACTION_DEAL, symbol=symbol, volume=volume,
                 type=side, price=price, deviation=10, magic=20261030,
                 comment=command['comment'][:31], type_time=mt5.ORDER_TIME_GTC)
    if closing:
        order['position'] = command['ticket']
    else:
        sl, tp = float(command['sl']), float(command['tp'])
        if not all(math.isfinite(v) and v > 0 for v in (volume, sl, tp)):
            raise ValueError('پارامتر سفارش نامعتبر است')
        if not (sl < tick.bid < tick.ask < tp if side == mt5.ORDER_TYPE_BUY else tp < tick.bid < tick.ask < sl):
            raise ValueError('حد سود یا حد ضرر نسبت به قیمت جاری معتبر نیست')
        loss = mt5.order_calc_profit(side, symbol, volume, price, sl)
        fees = volume * request['commission']
        # Hard cap independent of dashboard settings; recheck against broker equity.
        if loss is None or abs(loss) + fees > status['equity'] * min(request['risk_percent'], .25) / 100 + 1e-6:
            raise ValueError('ریسک سفارش از سقف محاسبه‌شده بیشتر است')
        order.update(sl=sl, tp=tp)
    # Select supported filling, then check exact request. Never retry order_send.
    fill = mt5.ORDER_FILLING_FOK if info.filling_mode & 1 else mt5.ORDER_FILLING_IOC if info.filling_mode & 2 else mt5.ORDER_FILLING_RETURN
    order['type_filling'] = fill
    check = mt5.order_check(order)
    if check is None or check.retcode != 0:
        raise ValueError(f"order_check: {getattr(check, 'comment', 'ناموفق')}")
    try:
        result = mt5.order_send(order)
    except Exception:
        return dict(uncertain=True, error='پاسخ ارسال نامشخص است؛ درخواست تکرار نمی‌شود')
    if result is None:
        return dict(uncertain=True, error='پاسخ ارسال نامشخص؛ بدون تکرار خودکار، حساب را بررسی کنید')
    success = result.retcode == mt5.TRADE_RETCODE_DONE
    known_rejections = {mt5.TRADE_RETCODE_REJECT, mt5.TRADE_RETCODE_INVALID,
        mt5.TRADE_RETCODE_INVALID_VOLUME, mt5.TRADE_RETCODE_INVALID_PRICE,
        mt5.TRADE_RETCODE_INVALID_STOPS, mt5.TRADE_RETCODE_NO_MONEY,
        mt5.TRADE_RETCODE_MARKET_CLOSED, mt5.TRADE_RETCODE_TRADE_DISABLED,
        mt5.TRADE_RETCODE_INVALID_FILL}
    return dict(ok=success, uncertain=not success and result.retcode not in known_rejections,
                retcode=result.retcode, comment=result.comment, order=result.order,
                deal=result.deal, price=result.price, volume=result.volume)


if __name__ == '__main__':
    try:
        result = run(json.load(sys.stdin))
    except Exception as e:
        result = dict(connected=False, error=str(e))
    sys.stdout.write(json.dumps(result, ensure_ascii=True, allow_nan=False))

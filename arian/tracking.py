"""Read-only attribution of external Arian EA positions; never manages orders."""
import hashlib
import math
from datetime import datetime, timezone
from .risk import session_key
from .clock import broker_to_utc


def reconcile_ea(store, config, feed, now):
    stream = 'mt5-ea-' + hashlib.sha256(feed['identity'].encode()).hexdigest()[:10]
    magics = set(config.get('observed_magic_numbers', []))
    if feed.get('deals') is None:
        return stream
    positions = {p['identifier']: p for p in feed.get('positions', []) if p['magic'] in magics}
    groups = {}
    for d in feed['deals']:
        if d.get('type') in (0, 1):
            groups.setdefault(d['position_id'], []).append(d)
    ids = set(positions) | {pid for pid, rows in groups.items()
        if any(d['magic'] in magics and d['entry'] in (0, 2) for d in rows)}
    aliases = {v: k for k, v in config['symbols'].items()}
    orders = {o['ticket']: o for o in feed.get('tracking_orders', [])}
    prior = {t['position_id']:t for t in store.trades(stream)}
    for pid in ids:
        rows = sorted(groups.get(pid, []), key=lambda d: (d['time_msc'], d['ticket']))
        entries = [d for d in rows if d['entry'] == 0 and d['magic'] in magics]
        p = positions.get(pid)
        previous = prior.get(pid)
        if not entries and p and previous:
            previous.update(status='OPEN',volume=p['volume'],sl=p['sl'],tp=p['tp'],
                            floating=p.get('profit',0)+p.get('swap',0))
            store.save_trade(previous)
            continue
        if not entries and not p:
            continue
        first = entries[0] if entries else None
        symbol = first['symbol'] if first else p['symbol']
        side = first['type'] if first else p['type']
        volume = sum(d['volume'] for d in entries) if entries else p['volume']
        entry = sum(d['price'] * d['volume'] for d in entries) / volume if entries else p['price_open']
        order = orders.get(first['order'], {}) if first else {}
        sl = order.get('sl', 0)
        spec = next((m.get('spec', {}) for m in feed.get('markets', {}).values()
                     if m.get('spec', {}).get('symbol') == symbol), {})
        # Historical initial SL only. Current/trailing stops must not invent initial R.
        risk = abs(entry - sl) * volume * spec.get('factor', 0) if sl > 0 else 0
        if not risk and previous:
            risk = previous.get('risk_money',0)
        exits = [d for d in rows if d['entry'] in (1, 3)]
        closed = not p and exits and sum(d['volume'] for d in exits) >= volume - 1e-8
        pnl = sum(d.get('profit', 0) + d.get('commission', 0) + d.get('swap', 0) + d.get('fee', 0) for d in rows)
        opened = first['time_msc'] / 1000 if first else broker_to_utc(p['time'],config.get('broker_clock',{'mode':'utc'}))
        complete = bool(entries and closed and not any(d['entry'] == 2 for d in rows))
        audit = feed.get('execution_audits',{}).get(str(pid),
            previous.get('execution_audit',{}) if previous else {})
        trade = dict(id=f'{stream}:{pid}', stream=stream, cycle_id=str(pid), position_id=pid,
            symbol=aliases.get(symbol, symbol), broker_symbol=symbol, direction='BUY' if side == 0 else 'SELL',
            status='BROKER_CLOSED' if closed else 'OPEN' if p else 'UNCERTAIN',
            opened=opened, closed=exits[-1]['time_msc']/1000 if closed else None,
            entry=entry, exit=exits[-1]['price'] if closed else None, volume=p['volume'] if p else volume,
            initial_volume=volume, sl=p['sl'] if p else sl, tp=p['tp'] if p else order.get('tp', 0),
            pnl=pnl, floating=p.get('profit', 0) + p.get('swap', 0) if p else 0,
            risk_money=risk, r=pnl/risk if risk > 0 and closed else None,
            learning_eligible=bool(complete and risk > 0 and math.isfinite(risk) and audit.get('status')=='consistent'),
            execution_audit=audit or dict(status='pending',reason='تطبیق قیمت اجرای بروکر هنوز انجام نشده'),
            policy='external-arian-ea', features=dict(symbol=aliases.get(symbol,symbol),
                setup='external-arian-ea', session=session_key(opened), spread_bucket='unknown'),
            cashflows=[dict(time=d['time_msc']/1000, amount=d.get('profit',0)+d.get('commission',0)+
                d.get('swap',0)+d.get('fee',0)) for d in rows],
            magic=first['magic'] if first else p['magic'])
        store.save_trade(trade)
    return stream


def ea_metrics(store, stream, now, initial):
    trades = store.trades(stream)
    closed = sorted((t for t in trades if t['status'] == 'BROKER_CLOSED'), key=lambda t:t['closed'])
    opened = [t for t in trades if t['status'] == 'OPEN']
    day = datetime.fromtimestamp(now, timezone.utc).date()
    today = lambda ts: datetime.fromtimestamp(ts, timezone.utc).date() == day
    realized = sum(t['pnl'] for t in trades)
    floating = sum(t['floating'] for t in opened)
    equity = initial + realized + floating
    daily_realized = sum(c['amount'] for t in trades for c in t.get('cashflows',[]) if today(c['time']))
    # Daily P&L is cash realized today plus current floating, explicitly labelled in UI.
    daily_pnl = daily_realized + floating
    peak = max(store.get(stream+':peak',initial), equity)
    store.set(stream+':peak',peak)
    day_base = max(0, initial + realized - daily_realized)
    streak = 0
    for t in reversed(closed):
        if not today(t['closed']) or t['pnl'] >= 0:
            break
        streak += 1
    rvalues = [t['r'] for t in closed if t.get('r') is not None]
    open_risk = sum(t.get('risk_money',0) for t in opened)
    last = max(trades,key=lambda t:t['opened'],default=None)
    before_last = initial + sum(t['pnl'] for t in closed if last and t['closed'] < last['opened'])
    return dict(initial=initial, balance=initial+realized, equity=equity, floating=floating,
        pnl=realized, daily_pnl=daily_pnl, realized_today=daily_realized,
        drawdown_percent=max(0,(peak-equity)/peak*100) if peak > 0 else 0,
        daily_drawdown_percent=max(0,-daily_pnl/day_base*100) if day_base > 0 else 0,
        trades_today=sum(today(t['opened']) for t in trades), closed_count=len(closed),
        closed_today=sum(today(t['closed']) for t in closed), open_count=len(opened),
        consecutive_losses=streak, win_rate=100*sum(t['pnl']>0 for t in closed)/len(closed) if closed else None,
        expectancy_r=sum(rvalues)/len(rvalues) if rvalues else None,
        review_count=sum(t.get('execution_audit',{}).get('status')=='quote_mismatch' for t in trades),
        pending_audit_count=sum(t.get('execution_audit',{}).get('status') in (None,'pending','unavailable') for t in trades),
        last_risk_money=last.get('risk_money',0) if last else None,
        last_risk_percent=last.get('risk_money',0)/before_last*100 if last and before_last>0 else None,
        open_risk_money=open_risk, open_risk_percent=open_risk/equity*100 if equity > 0 else None)

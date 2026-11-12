"""Read-only broker evidence, no order_send and no terminal launch."""
import json, time
from datetime import datetime,timezone,timedelta
from arian.windows_process import terminal_is_running
from arian.clock import broker_to_utc
import MetaTrader5 as mt5

path=r'C:\Program Files\MetaTrader 5\terminal64.exe'
if not terminal_is_running(path): raise SystemExit('Terminal is closed')
if not mt5.initialize(path=path,timeout=5000): raise SystemExit(str(mt5.last_error()))
try:
    a=mt5.account_info()
    now=datetime.now(timezone.utc)
    deals=mt5.history_deals_get(now-timedelta(days=35),now+timedelta(days=1))
    orders=mt5.history_orders_get(now-timedelta(days=35),now+timedelta(days=1))
    owned={d.position_id for d in deals if d.magic==777888 and d.entry==0}
    rows=[dict(d._asdict(),utc=broker_to_utc(d.time,{'mode':'fxpro-eu'})) for d in deals if d.position_id in owned]
    entries=[d for d in rows if d['entry']==0]
    last=max(entries,key=lambda d:d['time_msc']) if entries else None
    latest=max(rows,key=lambda d:d['time_msc']) if rows else None
    markets={}
    for symbol in ('GOLD','EURUSD','BITCOIN'):
        info=mt5.symbol_info(symbol);tick=mt5.symbol_info_tick(symbol)
        markets[symbol]=dict(tick=tick._asdict(),spec={k:getattr(info,k) for k in ('point','digits','volume_min','volume_step','trade_tick_size','trade_stops_level','expiration_mode','trade_mode')},tick_utc=broker_to_utc(tick.time,{'mode':'fxpro-eu'}))
    result=dict(balance=a.balance,last_entry=last,last_deal=latest,bot_deal_count=len(rows),
        recent_bot_orders=[o._asdict() for o in orders if o.magic==777888][-12:],
        positions=[p._asdict() for p in mt5.positions_get()],orders=[o._asdict() for o in mt5.orders_get()],markets=markets,
        host_epoch=time.time())
    print(json.dumps(result,indent=2))
finally: mt5.shutdown()

"""Export existing MT5 closed M1 history and broker specs, read only."""
import argparse
import csv
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from arian.clock import broker_to_utc


def main():
    import MetaTrader5 as mt5
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bars',type=int,default=10000)
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    config=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
    output=Path(args.out).resolve();output.mkdir(parents=True,exist_ok=True)
    if (output/'manifest.json').exists():raise SystemExit('Use a new output directory')
    if not mt5.initialize(path=config['terminal_path'],timeout=5000):raise SystemExit(str(mt5.last_error()))
    try:
        account=mt5.account_info()
        if not account or account.currency!='USD':raise ValueError('USD account required')
        if config['broker_clock']['mode']=='fxpro-eu' and not account.server.startswith('FxPro-'):raise ValueError('Broker clock profile mismatch')
        manifest=dict(markets={},broker=account.server,clock=config['broker_clock'])
        for canonical,symbol in config['symbols'].items():
            if not mt5.symbol_select(symbol,True):raise ValueError(f'Symbol not found: {symbol}')
            info,tick=mt5.symbol_info(symbol),mt5.symbol_info_tick(symbol)
            rates=mt5.copy_rates_from_pos(symbol,mt5.TIMEFRAME_M1,1,args.bars)
            if rates is None or len(rates)<400:raise ValueError(f'Insufficient terminal history: {symbol}')
            value=mt5.order_calc_profit(mt5.ORDER_TYPE_BUY,symbol,1.,tick.ask,tick.ask+info.trade_tick_size)
            if value is None or value<=0:raise ValueError('Unable to verify USD PnL factor')
            spec=dict(symbol=symbol,factor=value/info.trade_tick_size,point=info.point,tick_size=info.trade_tick_size,
                digits=info.digits,volume_min=info.volume_min,volume_max=info.volume_max,
                volume_step=info.volume_step,stops_level=info.trade_stops_level)
            path=output/(canonical+'_M1.csv')
            with path.open('w',encoding='utf-8',newline='') as file:
                writer=csv.writer(file);writer.writerow(['time','open','high','low','close','volume','spread_points'])
                for r in rates:writer.writerow([int(broker_to_utc(int(r['time']),config['broker_clock'])),
                    r['open'],r['high'],r['low'],r['close'],r['tick_volume'],r['spread']])
            manifest['markets'][canonical]=dict(csv=str(path),spec=spec,rows=len(rates))
            print(canonical, len(rates),'closed bars exported')
        (output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    finally:mt5.shutdown()


if __name__=='__main__':main()

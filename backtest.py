"""Chronological, bid-OHLC research replay. No broker orders or live DB access.

Costs/within-bar paths are approximations. If SL and TP occur in the same
bar, SL is processed first. Decisions use only bars closed before entry.
"""
import argparse
import csv
import hashlib
import json
import copy
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from arian.storage import Store
from arian.strategy import Bar, analyze, valid_bars
from arian.paper import PaperBroker
from arian.risk import entry_checks, session_key
from arian.profiles import apply_profile

ROOT = Path(__file__).resolve().parent


class ResearchLedger:
    """In-memory replay ledger; final results flushed to an isolated SQLite DB.

    Replay does not need a durable write for each historical price sample.
    The live engine always uses the durable Store instead.
    """
    def __init__(self):
        self.states, self.records, self.events = {}, {}, []

    def get(self,key,default=None):
        return copy.deepcopy(self.states.get(key,default))

    def set(self,key,value):
        self.states[key]=copy.deepcopy(value)

    def save_trade(self,trade):
        self.records[trade['id']]=copy.deepcopy(trade)

    def trades(self,stream=None,closed_only=False):
        return [copy.deepcopy(t) for t in self.records.values() if (not stream or t['stream']==stream)
                and (not closed_only or t['status']!='OPEN')]

    def seen(self,stream,cycle):
        return any(t['stream']==stream and t['cycle_id']==cycle for t in self.records.values())

    def event(self,ts,kind,payload):
        self.events.append((ts,kind,copy.deepcopy(payload)))

    def flush(self,path):
        store=Store(path)
        for key,value in self.states.items():store.set(key,value)
        for trade in self.records.values():store.save_trade(trade)
        for ts,kind,payload in self.events:store.event(ts,kind,payload)


def load_csv(path):
    rows = []
    with Path(path).open(encoding='utf-8-sig',newline='') as file:
        for row in csv.DictReader(file):
            stamp = row['time']
            ts = float(stamp) if stamp.replace('.','',1).isdigit() else datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp()
            if not stamp.replace('.','',1).isdigit() and datetime.fromisoformat(stamp.replace('Z','+00:00')).tzinfo is None:
                raise ValueError('CSV timestamps must specify UTC with Z or an explicit offset')
            bar = Bar(ts,*(float(row[k]) for k in ('open','high','low','close')),float(row.get('volume',0)))
            spread = float(row['spread_points'])
            if not 0 <= spread < 100000 or ts % 60 or not valid_bars([bar]):
                raise ValueError('Invalid M1 bar or spread')
            if rows and ts <= rows[-1][0].time:
                raise ValueError('CSV must be strictly chronological without duplicate timestamps')
            rows.append((bar,spread))
    return rows


def aggregate_m5(completed):
    groups = defaultdict(list)
    for bar in completed:
        groups[int(bar.time//300)*300].append(bar)
    return [Bar(ts,bs[0].open,max(b.high for b in bs),min(b.low for b in bs),bs[-1].close,sum(b.volume for b in bs))
            for ts,bs in sorted(groups.items()) if len(bs)==5 and bs[-1].time==ts+240]


def research_news_ok(now, events, unfiltered):
    if unfiltered:
        return True
    return not any(e['currency'] in ('USD','EUR') and abs(now-e['time']) <= 1200 for e in events)


def run(manifest, output, unfiltered=False, calendar_path=None, start=None, end=None, profile="conservative"):
    output = Path(output)
    output.mkdir(parents=True,exist_ok=True)
    db_path = output/'research.sqlite'
    if db_path.exists():
        raise ValueError('Output directory already has a research database; choose a new output directory')
    config = apply_profile(json.loads((ROOT/'config.json').read_text(encoding='utf-8')), profile)
    events = []
    if not unfiltered:
        if not calendar_path:
            raise ValueError('Supply --calendar with reviewed historical coverage or explicitly --unfiltered-news-research')
        calendar = json.loads(Path(calendar_path).read_text(encoding='utf-8'))
        events = calendar['events']
    store = ResearchLedger()
    broker = PaperBroker(store,config,'historical-research')
    timeline = defaultdict(dict)
    history = {symbol:deque(maxlen=1200) for symbol in manifest['markets']}
    provenance = {}
    last_by_symbol = {}
    for symbol, market in manifest['markets'].items():
        source = Path(market['csv'])
        provenance[symbol] = dict(csv=str(source),sha256=hashlib.sha256(source.read_bytes()).hexdigest())
        loaded = load_csv(source)
        if loaded:
            last_by_symbol[symbol] = loaded[-1][0].time
        for bar, spread in loaded:
            timeline[bar.time][symbol] = (bar,spread)
    if not timeline:
        raise ValueError('No bars')
    times=sorted(timeline)
    if not unfiltered and not calendar['coverage_start'] <= times[0] <= times[-1] + 60 <= calendar['coverage_end']:
        raise ValueError('Historical news calendar does not cover the data range')
    start = float(start) if start is not None else times[0]
    end = float(end) if end is not None else times[-1]+60
    samples=[]
    signal_count=0
    for now in times:
        for symbol,(bar,spread_points) in sorted(timeline[now].items()):
            spec=manifest['markets'][symbol]['spec']
            spread=spread_points*spec['point']
            tick=dict(time=now,bid=bar.open,ask=bar.open+spread)
            broker.mark(symbol,tick,now)
            m1=list(history[symbol])
            m5=aggregate_m5(m1)
            # m5 only contains complete groups ending strictly before current M1.
            report=analyze(symbol,m1[-220:],m5[-220:],config)
            candidate=report.get('candidate')
            if candidate and start<=now<end:
                signal_count+=1
                candidate['features'].update(session=session_key(now),spread_bucket='wide' if spread/candidate['atr']>.07 else 'tight')
                fresh = bool(m1 and now-m1[-1].time==60 and m5 and now-(m5[-1].time+300)<=330)
                checks=entry_checks(now,tick,candidate,config,broker.metrics(now),
                    {'ok':research_news_ok(now,events,unfiltered)},False,True)
                if fresh and all(c['ok'] for c in checks):
                    broker.open(candidate,tick,spec,now,policy='fixed-base-research')
            # Conservative OHLC execution. Spread is bar-level, not tick history.
            for trade in store.trades('historical-research'):
                if trade['status']!='OPEN' or trade['symbol']!=symbol:
                    continue
                buy=trade['direction']=='BUY'
                stop_hit=bar.low<=trade['sl'] if buy else bar.high+spread>=trade['sl']
                tp_hit=bar.high>=trade['tp'] if buy else bar.low+spread<=trade['tp']
                if stop_hit:
                    bid=trade['sl'] if buy else trade['sl']-spread
                    broker.mark(symbol,dict(time=now+59,bid=bid,ask=bid+spread),now+59)
                elif tp_hit:
                    bid=trade['tp'] if buy else trade['tp']-spread
                    broker.mark(symbol,dict(time=now+59,bid=bid,ask=bid+spread),now+59)
            broker.mark(symbol,dict(time=now+59,bid=bar.close,ask=bar.close+spread),now+59,
                        force=now+60>=end or now>=last_by_symbol[symbol])
            history[symbol].append(bar)
        if now>=start:
            metrics=broker.metrics(now+59)
            samples.append(dict(time=now+59,equity=metrics['equity'],drawdown_percent=metrics['drawdown_percent']))
    metrics=broker.metrics(times[-1]+59)
    trades=store.trades('historical-research')
    store.flush(db_path)
    report=dict(profile=profile,source='MT5 bid M1 OHLC research replay', provenance=provenance,
        data_start=times[0],data_end=times[-1]+60,evaluation_start=start,evaluation_end=end,
        bars=sum(len(v) for v in timeline.values()),signals=signal_count,metrics=metrics,
        max_drawdown_percent=max((s['drawdown_percent'] for s in samples),default=0),
        assumptions=dict(commission_per_lot=config['commission_per_lot_roundtrip_usd'],
            slippage_points=config['slippage_points'],bar_spread=True,ambiguous_bar='stop-first',
            news_filtered=not unfiltered),
        limitations=['OHLC paths, queue position and tick-level spread are unknown',
            'No calibrated whale data; price action proxy only',
            'No parameter optimization or live learning promotion from this replay',
            'Historical results do not establish future profitability'])
    (output/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    for name,rows in [('trades',trades),('equity',samples)]:
        if rows:
            with (output/(name+'.csv')).open('w',newline='',encoding='utf-8-sig') as file:
                writer=csv.DictWriter(file,fieldnames=list(rows[0]),extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True,help='JSON containing markets -> symbol -> csv and verified spec')
    parser.add_argument('--out',required=True)
    parser.add_argument('--calendar')
    parser.add_argument('--unfiltered-news-research',action='store_true')
    parser.add_argument('--start',help='Evaluation start: ISO date with explicit timezone')
    parser.add_argument('--end',help='Evaluation end (exclusive): ISO date with explicit timezone')
    args=parser.parse_args()
    parse=lambda x:datetime.fromisoformat(x.replace('Z','+00:00')).timestamp() if x else None
    report=run(json.loads(Path(args.manifest).read_text(encoding='utf-8')),args.out,
               args.unfiltered_news_research,args.calendar,parse(args.start),parse(args.end))
    print(json.dumps(report,indent=2,ensure_ascii=True))


if __name__=='__main__':main()

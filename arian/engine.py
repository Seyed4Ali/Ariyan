import copy
import hashlib
import threading
import time
import json
import math
from pathlib import Path
from .feed import MT5Feed
from .storage import Store
from .strategy import Bar, analyze, ema, atr, valid_bars
from .timeframes import TIMEFRAMES
from .risk import entry_checks, session_key
from .paper import PaperBroker
from .learning import Learner
from .news import news_status, save_calendar


class Engine:
    def __init__(self, config, db_path):
        self.config = config
        self.store = Store(db_path)
        self.feed = MT5Feed(config)
        self.paper = PaperBroker(self.store, config)
        self.baseline = PaperBroker(self.store, config, 'mt5-baseline')
        self.learner = Learner(self.store, config)
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.paused = self.store.get('paused', False)
        self.flatten = self.store.get('flatten_pending', False)
        self.demo_identity = None  # restart ALWAYS disarms broker execution
        self.feed_state = dict(connected=False, error='در حال بررسی ارتباط با متاتریدر…')
        self.markets = {}
        self.last_error = None
        self.capital_binding = None
        self.paper_identity = None
        self.last_decision = {}
        self.last_learning_count = -1
        self.updated = None
        self.started = time.time()
        self.store.event(self.started, 'START', dict(message='راه‌اندازی؛ اجرای بروکر غیرفعال، حالت شبیه‌سازی'))

    def start(self):
        self.thread = threading.Thread(target=self.loop, daemon=True, name='arian-engine')
        self.thread.start()

    def loop(self):
        while not self.stop.is_set():
            try:
                feed = self.feed.snapshot(history_since=self.store.get('demo_history_since', self.started - 86400))
                with self.lock:
                    self.step(feed, time.time())
                    self.last_error = None
            except Exception as e:
                with self.lock:
                    self.last_error = str(e)
                    self.paused = True
                    self.demo_identity = None
                    self.store.event(time.time(), 'ENGINE_ERROR', dict(message=str(e)))
            self.stop.wait(self.config.get('poll_seconds',1) if self.feed_state.get('connected') else 10)

    def demo_stream(self, identity):
        return 'mt5-demo-' + hashlib.sha256(identity.encode()).hexdigest()[:10]

    def bind_paper_account(self, feed, now):
        if self.config.get('capital_source') != 'connected-demo-balance':
            return True
        balance = feed.get('balance')
        if not feed.get('demo') or feed.get('currency') != 'USD' or not isinstance(balance,(int,float)) or not math.isfinite(balance) or balance <= 0:
            return False
        if self.paper_identity == feed['identity']:
            return True
        tag = hashlib.sha256(feed['identity'].encode()).hexdigest()[:10]
        key = 'capital-binding:' + tag
        binding = self.store.get(key)
        if not binding:
            binding = dict(source='connected-demo-balance', initial=balance, synced_at=now,
                           server=feed.get('server'), account_login=feed.get('account_login'))
            # Seed once. Future polls must never erase simulated losses/profits.
            values = {key:binding}
            for stream in ('mt5-paper-'+tag, 'mt5-baseline-'+tag):
                values[stream+':initial'] = balance
                values[stream+':peak'] = balance
            self.store.set_many(values)
            self.store.event(now,'CAPITAL_SYNC',dict(balance=balance,message='سرمایهٔ اولیه با حساب دموی متصل هم‌اندازه شد'))
        self.capital_binding, self.paper_identity = binding, feed['identity']
        self.paper = PaperBroker(self.store,self.config,'mt5-paper-'+tag)
        self.baseline = PaperBroker(self.store,self.config,'mt5-baseline-'+tag)
        self.learner = Learner(self.store,self.config,'learner:'+tag)
        self.last_learning_count = -1
        return True

    def chart_frames(self, raw, now):
        frames = {}
        for name, seconds in TIMEFRAMES.items():
            frame = raw.get('timeframes',{}).get(name,{})
            rows = frame.get('bars',[])
            bars = [Bar(**b) for b in rows]
            summary = dict(seconds=seconds, bars=rows[-100:], current=frame.get('current'),
                           trend='—', atr=None, fresh=False)
            if bars and valid_bars(bars):
                summary['fresh'] = 0 <= now-(bars[-1].time+seconds) <= seconds+75
                summary['atr'] = atr(bars)
                if len(bars)>=50:
                    closes=[b.close for b in bars]
                    fast,slow=ema(closes,20),ema(closes,50)
                    summary.update(ema20=fast,ema50=slow,
                        trend='صعودی' if closes[-1]>slow and fast>slow else 'نزولی' if closes[-1]<slow and fast<slow else 'خنثی')
            frames[name]=summary
        return frames

    def demo_metrics(self, feed, now):
        stream = self.demo_stream(feed['identity'])
        records = self.store.trades(stream)
        equity = feed['equity']
        key = stream + ':account'
        state = self.store.get(key, dict(initial=equity, peak=equity, day='', day_equity=equity))
        day = time.strftime('%Y-%m-%d', time.gmtime(now))
        if state['day'] != day:
            state.update(day=day, day_equity=equity)
        state['peak'] = max(state['peak'], equity)
        self.store.set(key, state)
        closed = sorted((t for t in records if t['status'] not in ('OPEN', 'SUBMITTING', 'UNCERTAIN')), key=lambda t: t['closed'])
        streak = 0
        for t in reversed(closed):
            if t['pnl'] >= 0 or time.strftime('%Y-%m-%d', time.gmtime(t['closed'])) != day:
                break
            streak += 1
        return dict(equity=equity, balance=feed['balance'],
                    daily_drawdown_percent=max(0, (state['day_equity'] - equity) / state['day_equity'] * 100) if state['day_equity'] > 0 else 100,
                    drawdown_percent=max(0, (state['peak'] - equity) / state['peak'] * 100) if state['peak'] > 0 else 100,
                    consecutive_losses=streak, open_count=len(feed.get('positions', [])) + feed.get('orders_count', 0),
                    trades_today=sum(time.strftime('%Y-%m-%d', time.gmtime(t['opened'])) == day for t in records),
                    last_exit=max((t['closed'] for t in closed), default=0))

    def reconcile_demo(self, feed, now):
        if feed.get('deals') is None:
            self.demo_identity = None
            return
        stream = self.demo_stream(feed['identity'])
        positions = [p for p in feed.get('positions', []) if p['magic'] == 20261030]
        for t in self.store.trades(stream):
            if t['status'] not in ('OPEN', 'SUBMITTING', 'UNCERTAIN'):
                continue
            matching = [p for p in positions if p['comment'] == 'AR:' + t['id'] or p['ticket'] == t.get('ticket')]
            if matching:
                p = matching[0]
                t.update(ticket=p['ticket'], position_id=p['identifier'], status='OPEN',
                         entry=p['price_open'], current=p['price_current'], floating=p['profit'], volume=p['volume'])
                self.store.save_trade(t)
                if self.store.get('demo_pending') == t['id']:
                    self.store.set('demo_pending', None)
                continue
            entries = [d for d in feed['deals'] if d['comment'] == 'AR:' + t['id'] and d['entry'] == 0]
            position_id = t.get('position_id') or (entries[0]['position_id'] if entries else None)
            deals = [d for d in feed['deals'] if position_id and d['position_id'] == position_id]
            exits = [d for d in deals if d['entry'] in (1, 2)]
            if exits:
                pnl = sum(d['profit'] + d['commission'] + d['swap'] + d.get('fee', 0) for d in deals)
                t.update(status='BROKER_CLOSED', closed=max(d['time_msc'] / 1000 for d in exits),
                         pnl=pnl, r=pnl / t['risk_money'], floating=0, exit=exits[-1]['price'])
                self.store.save_trade(t)
                if self.store.get('demo_pending') == t['id']:
                    self.store.set('demo_pending', None)
                self.store.event(now, 'DEMO_CLOSE', dict(symbol=t['symbol'], pnl=pnl))
            elif now - t['opened'] > 20:
                # Ambiguous submission is quarantined; never automatically resubmitted.
                t['status'] = 'UNCERTAIN'
                self.store.save_trade(t)
                self.demo_identity = None

    def step(self, feed, now):
        self.feed_state = feed
        self.updated = now
        if not feed.get('connected'):
            self.demo_identity = None
            for market in self.markets.values():
                market.update(decision='BLOCK', reason='ارتباط قیمت قطع است؛ ورود مسدود')
            return
        if self.demo_identity and self.demo_identity != feed['identity']:
            self.demo_identity = None
            self.paused = True
        capital_ready = self.bind_paper_account(feed, now)
        self.reconcile_demo(feed, now)
        news = news_status(self.store, now, self.config['news_buffer_minutes'])
        self.manage_demo(feed, now)
        for symbol, raw in feed.get('markets', {}).items():
            if raw.get('error'):
                self.markets[symbol] = dict(symbol=symbol, decision='BLOCK', reason=raw['error'], checks=[])
                continue
            tick, spec = raw['tick'], raw['spec']
            for broker in (self.paper, self.baseline):
                broker.mark(symbol, tick, now, force=self.flatten)
            m1 = [Bar(**b) for b in raw['m1']]
            m5 = [Bar(**b) for b in raw['m5']]
            report = analyze(symbol, m1, m5, self.config)
            report.update(tick=tick, spec=spec, decision='WAIT', timeframes=self.chart_frames(raw,now))
            candidate = report.get('candidate')
            fresh = bool(m1 and m5 and 0 <= now - tick['time'] <= self.config['max_tick_age_seconds']
                         and 0 <= now - (m1[-1].time + 60) <= 75
                         and 0 <= now - (m5[-1].time + 300) <= 330)
            report['feed_fresh'] = fresh
            if not fresh:
                report.update(decision='BLOCK', reason='بازار بسته یا قیمت/کندل قدیمی است')
                candidate = None
            if feed['currency'] != 'USD':
                report.update(decision='BLOCK', reason='در این نسخه محاسبهٔ هزینه فقط برای حساب USD فعال است')
                candidate = None
            if not capital_ready:
                report.update(decision='BLOCK',reason='هم‌اندازه‌سازی سرمایه به حساب دموی USD با موجودی معتبر نیاز دارد')
                candidate = None
            if candidate:
                candidate['features'].update(session=session_key(now), spread_bucket='wide' if
                    (tick['ask'] - tick['bid']) / candidate['atr'] > .07 else 'tight')
                metrics = self.paper.metrics(now)
                learned = self.learner.allow(candidate['features'])
                checks = entry_checks(now, tick, candidate, self.config, metrics, news, self.paused, learned)
                checks.append(dict(id='m5fresh', label='کندل M5 تازه است', ok=fresh))
                report['checks'].extend(checks)
                fail = next((c for c in checks if not c['ok']), None)
                report.update(decision='SKIP' if fail else 'TAKE', reason=fail['label'] if fail else 'همهٔ شروط پایه برقرار است')
                for broker, permit in ((self.baseline, True), (self.paper, learned)):
                    bchecks = entry_checks(now, tick, candidate, self.config, broker.metrics(now), news, self.paused, permit)
                    if all(c['ok'] for c in bchecks):
                        trade = broker.open(candidate, tick, spec, now,
                            policy='baseline' if broker is self.baseline else f"checklist-v{self.learner.state['version']}")
                        if broker is self.paper and not trade:
                            report.update(decision='SKIP', reason='سایکل تکراری، فاصلهٔ حد ضرر نامعتبر یا حداقل لات بیش از بودجهٔ ریسک است')
                if self.demo_identity:
                    self.maybe_open_demo(candidate, tick, spec, feed, news, fresh, now)
            stamp = m1[-1].time if m1 else 0
            signature = (stamp, report['decision'], report['reason'])
            if self.last_decision.get(symbol) != signature:
                self.store.event(now, 'DECISION', dict(symbol=symbol, decision=report['decision'], reason=report['reason']))
                self.last_decision[symbol] = signature
            self.markets[symbol] = report
        if self.flatten:
            paper_open = any(t['status'] == 'OPEN' for stream in (self.paper.stream, self.baseline.stream) for t in self.store.trades(stream))
            demo_open = any(p['magic'] == 20261030 for p in feed.get('positions', []))
            self.flatten = paper_open or demo_open
            self.store.set('flatten_pending', self.flatten)
        baseline = self.store.trades(self.baseline.stream, closed_only=True)
        if len(baseline) != self.last_learning_count:
            self.learner.update(baseline, self.store.trades(self.paper.stream), now)
            self.last_learning_count = len(baseline)

    def manage_demo(self, feed, now):
        # Restart/disarm suppresses entries, but tracked demo time exits remain managed.
        if not feed.get('demo'):
            return
        records = self.store.trades(self.demo_stream(feed['identity']))
        for t in records:
            if t['status'] != 'OPEN' or not t.get('ticket'):
                continue
            if self.flatten or now - t['opened'] >= self.config['max_hold_seconds']:
                result = self.feed.call(action='demo_close', identity=feed['identity'], command=dict(t, comment='AR:exit'),
                                        commission=self.config['commission_per_lot_roundtrip_usd'], risk_percent=self.config['risk_percent'])
                self.store.event(now, 'DEMO_EXIT_REQUEST', dict(id=t['id'], result=result))
                if not result.get('ok'):
                    self.demo_identity = None

    def maybe_open_demo(self, candidate, tick, spec, feed, news, fresh, now):
        metrics = self.demo_metrics(feed, now)
        checks = entry_checks(now, tick, candidate, self.config, metrics, news, self.paused, True)
        if not fresh or not feed['demo'] or not all(c['ok'] for c in checks):
            return
        stream = self.demo_stream(feed['identity'])
        existing = self.store.trades(stream)
        if any(t['status'] in ('OPEN', 'UNCERTAIN', 'SUBMITTING') for t in existing):
            return
        # Use sizing implementation without writing a fake fill into real/demo ledger.
        scratch = self.store.get('demo_pending')
        if scratch:
            self.demo_identity = None
            return
        planner = PaperBroker(self.store, self.config, stream)
        demo_config = dict(self.config, risk_percent=min(self.config['risk_percent'],
            max(0, self.config['daily_loss_percent'] - metrics['daily_drawdown_percent'])))
        planner.config = demo_config
        planned = planner.open(candidate, tick, spec, now, policy='fixed-base-demo', equity=feed['equity'], persist=False)
        if not planned:
            return
        planned.update(stream=stream, status='SUBMITTING')
        self.store.save_trade(planned)
        self.store.set('demo_pending', planned['id'])
        # Journal intent before sending. Timeouts/partial fills NEVER retry entry.
        result = self.feed.call(action='demo_open', identity=self.demo_identity,
            command=dict(planned, comment='AR:' + planned['id']),
            commission=self.config['commission_per_lot_roundtrip_usd'], risk_percent=self.config['risk_percent'])
        self.store.event(now, 'DEMO_SUBMIT', dict(id=planned['id'], result=result))
        if result.get('ok'):
            planned.update(status='OPEN', ticket=result['order'], entry=result['price'], volume=result['volume'])
            self.store.set('demo_pending', None)
        elif result.get('uncertain'):
            planned['status'] = 'UNCERTAIN'
            self.demo_identity = None
        else:
            planned.update(status='REJECTED', closed=now, pnl=0, r=0)
            self.store.set('demo_pending', None)
            self.demo_identity = None
        self.store.save_trade(planned)

    def action(self, action, payload):
        with self.lock:
            now = time.time()
            if action == 'pause':
                self.paused = True
                self.store.set('paused', True)
            elif action == 'resume':
                self.paused = False
                self.store.set('paused', False)
            elif action == 'flatten':
                self.paused, self.flatten = True, True
                self.store.set('paused', True)
                self.store.set('flatten_pending', True)
            elif action == 'rollback':
                self.learner.rollback()
            elif action == 'calendar':
                save_calendar(self.store, payload, now)
            elif action == 'demo_disarm':
                self.demo_identity = None
            elif action == 'demo_arm':
                f = self.feed_state
                if payload.get('confirmation') != 'DEMO':
                    raise ValueError('برای فعال‌سازی فقط حساب دمو، کلمهٔ DEMO را وارد کنید')
                if not f.get('connected') or not f.get('demo') or f.get('currency') != 'USD':
                    raise ValueError('حساب دمو با ارز USD باید در متاتریدر متصل باشد')
                if not f.get('trade_allowed') or f.get('positions') or f.get('orders_count'):
                    raise ValueError('Algo Trading را روشن و یک حساب دموی اختصاصی بدون پوزیشن انتخاب کنید')
                if self.store.get('demo_pending') or any(t['status'] in ('UNCERTAIN', 'SUBMITTING') for t in self.store.trades(self.demo_stream(f['identity']))):
                    raise ValueError('ارسال قبلی نامشخص است؛ اول وضعیت حساب و ژورنال بررسی شود')
                self.demo_identity = f['identity']
                self.store.set('demo_history_since', min(self.store.get('demo_history_since', now), now))
                self.demo_metrics(f, now)
            else:
                raise ValueError('فرمان ناشناخته')
            self.store.event(now, 'CONTROL', dict(action=action))
            return dict(ok=True)

    def snapshot(self):
        with self.lock:
            now = time.time()
            f = self.feed_state
            connection = {k: f.get(k) for k in ('connected', 'error', 'server', 'currency', 'demo', 'trade_allowed', 'fetched','broker_offset_seconds',
                'account_login','balance','equity','profit','margin_free')}
            trades = self.store.trades(self.paper.stream)
            demo_trades = self.store.trades(self.demo_stream(f['identity'])) if f.get('identity') else []
            try:
                research = json.loads((Path(__file__).resolve().parents[1] / 'reports' / 'broker-sample' / 'report.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                research = None
            return copy.deepcopy(dict(name='Arian • Trading Lab', now=now, updated=self.updated,
                mode='DEMO' if self.demo_identity else 'SHADOW', paused=self.paused, real_trading=False,
                connection=connection, markets=self.markets, account=self.paper.metrics(now),
                baseline=self.baseline.metrics(now), trades=trades[-150:], demo_trades=demo_trades[-100:],
                learner=self.learner.snapshot(self.store.trades(self.baseline.stream, closed_only=True)),
                news=news_status(self.store, now, self.config['news_buffer_minutes']),
                events=self.store.events(), config={k: v for k, v in self.config.items() if k not in ('terminal_path',)},
                error=self.last_error, flatten_pending=self.flatten, research=research, capital_binding=self.capital_binding,
                supported_timeframes=list(TIMEFRAMES)))

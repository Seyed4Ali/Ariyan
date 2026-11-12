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
from .tracking import reconcile_ea, ea_metrics
from .calendar_feed import CalendarFeed
from .ea_control import EAControl
from .profiles import apply_profile
from .runtime_clock import RuntimeClock


class Engine:
    def __init__(self, config, db_path):
        self.clock = RuntimeClock(config.get("runtime_clock", "system"))
        self.base_config = dict(config)
        self.store = Store(db_path)
        self.config = config = apply_profile(config, self.store.get("execution_profile", "conservative"))
        self.feed = MT5Feed(config)
        self.paper = PaperBroker(self.store, config)
        self.baseline = PaperBroker(self.store, config, 'mt5-baseline')
        self.learner = Learner(self.store, config)
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.paused = self.store.get('paused', False)
        self.flatten = self.store.get('flatten_pending', False)
        self.monitoring = self.store.get('monitoring', True)
        self.ea_control = EAControl(self.store,config.get('observed_magic_numbers',[]),config['symbols'].values())
        self.demo_identity = None  # restart ALWAYS disarms broker execution
        self.risk_override_identity = None  # session-only, scoped to the current demo account
        self.feed_state = dict(connected=False, error='در حال بررسی ارتباط با متاتریدر…')
        self.markets = {}
        self.last_error = None
        self.pause_reason = None
        self.last_order_attempt = None
        self.capital_binding = None
        self.paper_identity = None
        self.last_decision = {}
        self.last_learning_count = -1
        self.tracking_stream = None
        self.ea_learner = None
        self.tracking_signature = None
        self.calendar_feed = CalendarFeed(self.store,self.clock.now)
        self.updated = None
        if not self.monitoring:
            self.feed_state=dict(connected=False,error='پایش متوقف است؛ متاتریدر خودکار باز نمی‌شود')
        self.started = self.clock.now()
        # Restore the selected local ledger without touching MT5 when monitoring is off.
        target=self.store.get('ea_control_target',{})
        identity=self.store.get('last_account_identity') or (
            f"{target['server']}:{target['login']}" if target else None)
        if identity:
            tag=hashlib.sha256(identity.encode()).hexdigest()[:10]
            binding=self.store.get('capital-binding:'+tag)
            if binding:
                self.paper_identity,self.capital_binding=identity,binding
                self.paper=PaperBroker(self.store,self.config,'mt5-paper-'+tag)
                self.baseline=PaperBroker(self.store,self.config,'mt5-baseline-'+tag)
                self.learner=Learner(self.store,self.config,'learner:'+tag)
                self.tracking_stream='mt5-ea-'+tag
                self.ea_learner=Learner(self.store,dict(self.config,auto_apply_shadow_learning=False),
                    'learner:'+self.tracking_stream+':quote-v1')
        self.store.event(self.started, 'START', dict(message='راه‌اندازی؛ اجرای بروکر غیرفعال، حالت شبیه‌سازی'))

    def start(self):
        threading.Thread(target=self.clock.loop,args=(self.stop,),daemon=True,name="verified-utc").start()
        threading.Thread(target=self.calendar_feed.loop, args=(self.stop,), daemon=True, name='calendar-feed').start()
        self.thread = threading.Thread(target=self.loop, daemon=True, name='arian-engine')
        self.thread.start()

    def loop(self):
        while not self.stop.is_set():
            if not self.monitoring:
                self.stop.wait(1)
                continue
            try:
                since = min(self.store.get('demo_history_since', self.started),
                            self.started - self.config.get('tracking_history_days',30)*86400)
                self.config['verified_utc_offset_seconds'] = self.clock.now()-time.time()
                feed = self.feed.snapshot(history_since=since)
                with self.lock:
                    self.step(feed, self.clock.now())
                    self.last_error = None
            except Exception as e:
                with self.lock:
                    self.last_error = str(e)
                    self.pause_reason = str(e)
                    self.paused = True
                    self.demo_identity = None
                    self.store.event(self.clock.now(), 'ENGINE_ERROR', dict(message=str(e)))
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
        self.store.set('last_account_identity',feed['identity'])
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
        if self.tracking_stream:
            records += self.store.trades(self.tracking_stream)
        session = self.store.get(stream + ':risk_session')
        if session:
            records = [t for t in records if t['opened'] >= session['started']]
        equity = feed['equity']
        key = stream + ':account'
        initial = session['initial'] if session else (self.capital_binding or {}).get('initial', equity)
        state = self.store.get(key, dict(initial=initial, peak=max(initial, equity), day='', day_equity=equity))
        if not session:
            state['peak'] = max(state['peak'], initial, self.store.get(self.tracking_stream+':peak',0) if self.tracking_stream else 0)  # changing executor cannot reset account drawdown
        day = time.strftime('%Y-%m-%d', time.gmtime(now))
        if state['day'] != day:
            state.update(day=day, day_equity=equity)
        state['peak'] = max(state['peak'], equity)
        self.store.set(key, state)
        closed = sorted((t for t in records if t['status'] not in ('OPEN', 'SUBMITTING', 'UNCERTAIN', 'REJECTED')), key=lambda t: t['closed'])
        streak = 0
        for t in reversed(closed):
            if t['pnl'] >= 0 or time.strftime('%Y-%m-%d', time.gmtime(t['closed'])) != day:
                break
            streak += 1
        return dict(equity=equity, balance=feed['balance'],
                    daily_drawdown_percent=max(0, (state['day_equity'] - equity) / state['day_equity'] * 100) if state['day_equity'] > 0 else 100,
                    drawdown_percent=max(0, (state['peak'] - equity) / state['peak'] * 100) if state['peak'] > 0 else 100,
                    initial=state['initial'], peak=state['peak'], pnl=equity-state['initial'], daily_pnl=equity-state['day_equity'],
                    floating=feed.get('profit',0), closed_count=len(closed),
                    win_rate=100*sum(t['pnl']>0 for t in closed)/len(closed) if closed else None,
                    expectancy_r=sum(t.get('r') or 0 for t in closed)/len(closed) if closed else None,
                    consecutive_losses=streak, open_count=len(feed.get('positions', [])) + feed.get('orders_count', 0),
                    trades_today=sum(t['status'] != 'REJECTED' and time.strftime('%Y-%m-%d', time.gmtime(t['opened'])) == day for t in records),
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
        if feed.get('identity') and feed.get('identity')!=self.feed_state.get('identity'):
            self.risk_override_identity=None
        self.feed_state = feed
        self.updated = now
        if not feed.get('connected'):
            self.ea_control.renew(dict(allowed=False,reasons=['ارتباط متاتریدر قطع است']),self.config['risk_percent'])
            self.demo_identity = None
            for market in self.markets.values():
                market.update(decision='BLOCK', reason='ارتباط قیمت قطع است؛ ورود مسدود')
            return
        if self.demo_identity and self.demo_identity != feed['identity']:
            self.demo_identity = None
            self.paused = True
        capital_ready = self.bind_paper_account(feed, now)
        self.ea_control.bind(feed)
        self.reconcile_demo(feed, now)
        self.tracking_stream = reconcile_ea(self.store, self.config, feed, now)
        self.ea_control.renew(self.ea_gate(now),self.config['risk_percent'])
        if self.ea_learner is None or self.ea_learner.state_key != 'learner:' + self.tracking_stream + ':quote-v1':
            self.ea_learner = Learner(self.store, dict(self.config, auto_apply_shadow_learning=False),
                                      'learner:' + self.tracking_stream + ':quote-v1')
            self.tracking_signature = None
        samples = [t for t in self.store.trades(self.tracking_stream) if t.get('learning_eligible')]
        signature = tuple((t['id'],t['pnl'],t['risk_money']) for t in samples)
        if signature != self.tracking_signature:
            self.ea_learner.update(samples, [], now)
            self.tracking_signature = signature
        news = news_status(self.store, now, self.config['news_buffer_minutes'], self.config.get('news_mode') == 'automatic')
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
            fresh = bool(self.clock.ready() and m1 and m5 and 0 <= now - tick['time'] <= self.config['max_tick_age_seconds']
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
                metrics = self.demo_metrics(feed, now) if self.demo_identity else self.paper.metrics(now)
                learned = self.learner.allow(candidate['features'])
                checks = entry_checks(now, tick, candidate, self.config, metrics, news, self.paused, True if self.demo_identity else learned)
                checks.append(dict(id='m5fresh', label='کندل M5 تازه است', ok=fresh))
                report['checks'].extend(checks)
                fail = next((c for c in checks if not c['ok']), None)
                report.update(decision='SKIP' if fail else 'TAKE', reason=fail['label'] if fail else 'همهٔ شروط پایه برقرار است')
                for broker, permit in ((self.baseline, True), (self.paper, learned)):
                    bchecks = entry_checks(now, tick, candidate, self.config, broker.metrics(now), news, self.paused, permit)
                    if all(c['ok'] for c in bchecks):
                        trade = broker.open(candidate, tick, spec, now,
                            policy='baseline' if broker is self.baseline else f"checklist-v{self.learner.state['version']}")
                        if broker is self.paper and not trade and not self.demo_identity:
                            report.update(decision='SKIP', reason='سایکل تکراری، فاصلهٔ حد ضرر نامعتبر یا حداقل لات بیش از بودجهٔ ریسک است')
                if self.demo_identity:
                    outcome = self.maybe_open_demo(candidate, tick, spec, feed, news, fresh, now)
                    if outcome:
                        report.update(decision='TAKE' if outcome['ok'] else 'SKIP', reason=outcome['reason'])
            stamp = m1[-1].time if m1 else 0
            signature = (stamp, report['decision'], report['reason'])
            if self.last_decision.get(symbol) != signature:
                self.store.event(now, 'DECISION', dict(symbol=symbol, decision=report['decision'], reason=report['reason']))
                self.last_decision[symbol] = signature
            self.markets[symbol] = report
        if self.flatten:
            paper_open = any(t['status'] == 'OPEN' for stream in (self.paper.stream, self.baseline.stream) for t in self.store.trades(stream))
            demo_open = any(p['magic'] == 20261030 for p in feed.get('positions', []))
            ea_open=any(p['magic'] in self.config.get('observed_magic_numbers',[]) for p in feed.get('positions',[]))
            ea_pending=bool(feed.get('observed_orders'))
            ea_ack=self.ea_control.snapshot()['acknowledged'] or not self.config.get('observed_magic_numbers')
            self.flatten = paper_open or demo_open or ea_open or ea_pending or not ea_ack
            self.store.set('flatten_pending', self.flatten)
            if not self.flatten:
                self.monitoring=False
                self.store.set('monitoring',False)
                self.feed_state=dict(connected=False,error='توقف و خروج انجام شد؛ پایش متاتریدر متوقف است')
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
        if not self.clock.ready() or not fresh or not feed['demo'] or not all(c['ok'] for c in checks):
            return dict(ok=False, reason=next((c['label'] for c in checks if not c['ok']), 'خوراک یا ساعت معتبر نیست'))
        stream = self.demo_stream(feed['identity'])
        existing = self.store.trades(stream)
        if any(t['status'] in ('OPEN', 'UNCERTAIN', 'SUBMITTING') for t in existing):
            return dict(ok=False, reason='پوزیشن یا ارسال در انتظار تطبیق بروکر وجود دارد')
        # Use sizing implementation without writing a fake fill into real/demo ledger.
        scratch = self.store.get('demo_pending')
        if scratch:
            self.demo_identity = None
            return
        planner = PaperBroker(self.store, self.config, stream)
        demo_config = dict(self.config, risk_percent=min(self.config['risk_percent'],
            max(0, self.config['daily_loss_percent'] - metrics['daily_drawdown_percent'])))
        planner.config = demo_config
        planned = planner.open(candidate, tick, spec, now, policy='fixed-base-demo', equity=feed['equity'], persist=False, metrics=metrics)
        if not planned:
            return dict(ok=False, reason='سایکل قبلاً مصرف شده، فاصلهٔ حد ضرر نامعتبر است یا حجم کمتر از حداقل لات است')
        planned.update(stream=stream, status='SUBMITTING')
        self.store.save_trade(planned)
        self.store.set('demo_pending', planned['id'])
        # Journal intent before sending. Timeouts/partial fills NEVER retry entry.
        result = self.feed.call(action='demo_open', identity=self.demo_identity,
            command=dict(planned, comment='AR:' + planned['id']),
            commission=self.config['commission_per_lot_roundtrip_usd'], risk_percent=self.config['risk_percent'])
        self.last_order_attempt = dict(time=now, symbol=planned['symbol'], id=planned['id'], result=result)
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
            # A broker-confirmed rejection consumes this signal, not the armed session.
            if not result.get('retcode') or result.get('uncertain') is not False:
                self.demo_identity = None
        self.store.save_trade(planned)
        return dict(ok=bool(result.get('ok')), reason='سفارش دموی بروکر پذیرفته شد' if result.get('ok') else result.get('error') or result.get('comment') or 'ارسال سفارش نیازمند بررسی است')

    def risk_budget_enabled(self):
        f=self.feed_state
        return not (self.risk_override_identity and self.risk_override_identity==f.get('identity') and f.get('demo') and f.get('currency')=='USD')

    def ea_gate(self, now):
        f=self.feed_state
        reasons=[]
        risk_budget=0
        if not f.get('connected') or not f.get('demo') or f.get('currency')!='USD':
            reasons.append('متاتریدر باید به حساب دموی USD متصل باشد')
        if not f.get('trade_allowed'): reasons.append('Algo Trading خاموش است')
        if self.flatten: reasons.append('خروج از معاملات هنوز تکمیل نشده است')
        if f.get('deals') is None or not f.get('tracking_history_complete',False):
            reasons.append('تاریخچهٔ معاملات و سفارش‌ها کامل دریافت نشده است')
        if self.tracking_stream and self.capital_binding:
            m=ea_metrics(self.store,self.tracking_stream,now,self.capital_binding['initial'])
            enabled=self.risk_budget_enabled()
            risk_budget=max(0,min(self.config['risk_percent'],self.config['daily_loss_percent']-m['daily_drawdown_percent'],self.config['max_drawdown_percent']-m['drawdown_percent'])) if enabled else self.config['risk_percent']
            if enabled and m['daily_drawdown_percent']>=self.config['daily_loss_percent']:
                reasons.append(f"حد زیان روزانه: {m['daily_drawdown_percent']:.2f}٪ / {self.config['daily_loss_percent']}٪ (روز UTC)")
            if enabled and m['drawdown_percent']>=self.config['max_drawdown_percent']:
                reasons.append(f"حد افت سرمایه: {m['drawdown_percent']:.2f}٪ / {self.config['max_drawdown_percent']}٪")
            if m['trades_today']>=self.config['max_trades_day']: reasons.append('سقف تعداد ورود امروز پر شده است')
            if m['consecutive_losses']>=self.config['max_consecutive_losses']: reasons.append('سقف زیان متوالی امروز پر شده است')
        else: reasons.append('دفتر ریسک حساب هنوز آماده نیست')
        if self.tracking_stream:
            closed=[t for t in self.store.trades(self.tracking_stream) if t.get('closed')]
            if closed and now-max(t['closed'] for t in closed)<self.config['cooldown_seconds']:
                reasons.append('وقفهٔ پس از آخرین خروج هنوز تمام نشده است')
        return dict(allowed=not reasons,reasons=reasons,risk_budget=risk_budget)

    def action(self, action, payload):
        with self.lock:
            now = self.clock.now()
            if action == 'set_profile':
                name = payload.get('profile')
                if name != 'conservative' and (not self.feed_state.get('connected') or not self.feed_state.get('demo')):
                    raise ValueError('پروفایل سریع فقط برای حساب دموی متصل فعال می‌شود')
                config = apply_profile(self.base_config, name)
                self.demo_identity = None
                self.ea_control.request('PAUSE')
                self.config = config
                for component in (self.feed, self.paper, self.baseline, self.learner):
                    component.config = config
                self.store.set('execution_profile', name)
            elif action == 'pause':
                self.paused = True
                self.demo_identity = None
                self.store.set('paused', True)
                self.ea_control.request('PAUSE')
            elif action == 'risk_budget':
                enabled=payload.get('enabled')
                if not isinstance(enabled,bool): raise ValueError('وضعیت بودجهٔ ریسک باید روشن یا خاموش باشد')
                f=self.feed_state
                if not enabled and (not f.get('connected') or not f.get('demo') or f.get('currency')!='USD' or not f.get('identity')):
                    raise ValueError('خاموش‌کردن بودجه فقط برای حساب دموی USD متصل مجاز است')
                self.risk_override_identity=None if enabled else f['identity']
                # A settings change never starts orders or carries an existing RUN lease.
                self.ea_control.request('PAUSE')
                self.store.event(now,'RISK_BUDGET',dict(enabled=enabled,message='سقف زیان و افت اکسپرت دمو '+('فعال شد' if enabled else 'برای این نشست خاموش شد')))
            elif action == 'ea_resume':
                if self.flatten: raise ValueError('ابتدا تکمیل توقف و خروج را منتظر بمانید')
                if not self.feed_state.get('connected') or not self.feed_state.get('demo'):
                    raise ValueError('ابتدا متاتریدر را باز کنید و به حساب دمو متصل شوید')
                if not self.updated or now-self.updated>10:
                    raise ValueError('وضعیت حساب تازه نیست؛ شروع مسدود است')
                self.demo_identity=None
                self.paused=False
                self.monitoring=True
                self.store.set_many(dict(paused=False,monitoring=True))
                self.ea_control.start()
                self.ea_control.renew(self.ea_gate(now),self.config['risk_percent'])
            elif action == 'resume':
                self.pause_reason = None
                self.paused = False
                self.store.set('paused', False)
                self.monitoring=True
                self.store.set('monitoring',True)
                # Resuming Python must never enable entries in an external EA.
                self.ea_control.request('PAUSE')
            elif action == 'monitor_resume':
                self.monitoring=True
                self.store.set('monitoring',True)
            elif action == 'flatten':
                self.paused, self.flatten = True, True
                self.demo_identity=None
                self.monitoring=True
                self.store.set('paused', True)
                self.store.set('flatten_pending', True)
                self.store.set('monitoring',True)
                self.ea_control.request('FLATTEN')
            elif action == 'rollback':
                self.learner.rollback()
            elif action == 'calendar':
                save_calendar(self.store, payload, now)
            elif action == 'calendar_refresh':
                self.calendar_feed.refresh_requested.set()
            elif action == 'demo_new_session':
                f = self.feed_state
                if payload.get('confirmation') != 'NEW DEMO':
                    raise ValueError('برای شروع آزمون تازه، NEW DEMO لازم است')
                if (not f.get('connected') or not f.get('demo') or f.get('currency') != 'USD'
                        or not self.updated or not 0 <= now-self.updated <= 10):
                    raise ValueError('حساب دموی USD با وضعیت تازه لازم است')
                stream = self.demo_stream(f['identity'])
                if (f.get('positions') or f.get('orders_count') or self.store.get('demo_pending')
                        or any(t['status'] in ('OPEN','SUBMITTING','UNCERTAIN') for t in self.store.trades(stream))):
                    raise ValueError('آزمون تازه فقط پس از بسته‌شدن سفارش‌ها و رفع ارسال مبهم مجاز است')
                equity = f.get('equity')
                if not isinstance(equity,(int,float)) or not math.isfinite(equity) or equity <= 0:
                    raise ValueError('موجودی حساب معتبر نیست')
                self.demo_identity = None
                self.ea_control.request('PAUSE')
                session = dict(started=now, initial=equity, label='آزمون تازهٔ دمو؛ سوابق قبلی محفوظ')
                self.store.set_many({stream+':risk_session':session,stream+':account':dict(
                    initial=equity,peak=equity,day='',day_equity=equity)})
                self.store.event(now,'DEMO_NEW_SESSION',session)
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
                if not self.updated or not 0 <= now-self.updated <= 10:
                    raise ValueError('وضعیت حساب تازه نیست؛ شروع مسدود است')
                if self.flatten:
                    raise ValueError('ابتدا خروج در انتظار را تکمیل کنید')
                self.ea_control.request('PAUSE')
                self.paused = False
                self.monitoring = True
                self.store.set_many(dict(paused=False, monitoring=True))
                self.demo_identity = f['identity']
                self.store.set('demo_history_since', min(self.store.get('demo_history_since', now), now))
                self.demo_metrics(f, now)
            else:
                raise ValueError('فرمان ناشناخته')
            self.store.event(now, 'CONTROL', dict(action=action))
            return dict(ok=True)

    def demo_blockers(self, now):
        f = self.feed_state
        if not f.get('connected') or not f.get('identity'):
            return []
        m = self.demo_metrics(f, now)
        reasons = []
        for key, limit, reason in (
                ('daily_drawdown_percent','daily_loss_percent','سقف زیان روزانه فعال است'),
                ('drawdown_percent','max_drawdown_percent','سقف افت سرمایه فعال است'),
                ('consecutive_losses','max_consecutive_losses','توقف حفاظتی پس از زیان‌های متوالی امروز'),
                ('trades_today','max_trades_day','سقف تعداد ورود امروز پر شده است')):
            if m[key] >= self.config[limit]:
                reasons.append(reason)
        if m['open_count']:
            reasons.append('تا بسته‌شدن پوزیشن یا سفارش جاری، ورود بعدی منتظر می‌ماند')
        if now-m['last_exit'] < self.config['cooldown_seconds']:
            reasons.append('فاصلهٔ پس از آخرین خروج هنوز تمام نشده است')
        return reasons

    def snapshot(self):
        with self.lock:
            now = self.clock.now()
            f = self.feed_state
            connection = {k: f.get(k) for k in ('connected', 'error', 'server', 'currency', 'demo', 'trade_allowed', 'fetched','broker_offset_seconds',
                'account_login','balance','equity','profit','margin_free')}
            trades = self.store.trades(self.paper.stream)
            demo_trades = self.store.trades(self.demo_stream(f['identity'])) if f.get('identity') else []
            tracking = None
            if self.tracking_stream:
                observed = self.store.trades(self.tracking_stream)
                samples = [t for t in observed if t.get('learning_eligible')]
                tracking = dict(source='external-ea', label='اکسپرت آریان در متاتریدر',
                    magic_numbers=self.config.get('observed_magic_numbers',[]),
                    metrics=ea_metrics(self.store,self.tracking_stream,now,
                        self.capital_binding['initial'] if self.capital_binding else self.config['starting_balance']),
                    trades=observed[-300:], connected=bool(f.get('connected')),
                    learner=dict(self.ea_learner.snapshot(samples), observed_closed=sum(t['status']=='BROKER_CLOSED' for t in observed),
                        eligible_count=len(samples), excluded_count=sum(t['status']=='BROKER_CLOSED' and not t.get('learning_eligible') for t in observed),
                        source='external-ea', application='observe-only'))
                tracking['learner']['disclaimer'] = 'تحلیل آماری معاملات اکسپرت؛ بدون تغییر پارامترها یا سفارش‌های MT5'
                if tracking['learner']['phase']=='collecting' and len(samples)<100:
                    tracking['learner']['message'] = f'{len(samples)} معاملهٔ بسته‌شدهٔ اکسپرت با ریسک اولیهٔ معتبر ثبت شد؛ برای ساخت فرضیه ۱۰۰ نمونه لازم است'
            try:
                research = json.loads((Path(__file__).resolve().parents[1] / 'reports' / 'broker-sample' / 'report.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                research = None
            return copy.deepcopy(dict(name='Arian • Trading Lab', now=now, updated=self.updated,
                mode='DEMO' if self.demo_identity else 'SHADOW', paused=self.paused, real_trading=False,
                monitoring=self.monitoring,ea_control=dict(self.ea_control.snapshot(),readiness=self.ea_gate(now),risk_budget_enabled=self.risk_budget_enabled()),
                connection=connection, markets=self.markets, account=self.paper.metrics(now), tracking=tracking,
                demo_account=self.demo_metrics(f,now) if f.get('connected') and f.get('identity') else None,
                risk_session=self.store.get(self.demo_stream(f['identity'])+':risk_session') if f.get('identity') else None,
                baseline=self.baseline.metrics(now), trades=trades[-150:], demo_trades=demo_trades[-100:],
                learner=self.learner.snapshot(self.store.trades(self.baseline.stream, closed_only=True)),
                news=news_status(self.store, now, self.config['news_buffer_minutes'], self.config.get('news_mode') == 'automatic'),
                events=self.store.events(), config={k: v for k, v in self.config.items() if k not in ('terminal_path','verified_utc_offset_seconds')},
                error=self.last_error, pause_reason=self.pause_reason, flatten_pending=self.flatten, research=research, capital_binding=self.capital_binding,
                clock=self.clock.snapshot(),
                execution=dict(profile=self.config.get('execution_profile'), strategy=self.config.get('strategy', 'liquidity-trend-v1'),
                    broker_armed=bool(self.demo_identity), last_order_attempt=self.last_order_attempt, tick_ages={s:now-m['tick']['time'] for s,m in self.markets.items() if m.get('tick')},
                    blockers=([f.get('error') or 'ارتباط متاتریدر قطع است'] if not f.get('connected') else [])
                        + (['ساعت شبکه هنوز تأیید نشده است'] if not self.clock.ready() else [])
                        + (['ورود متوقف است'+(': '+self.pause_reason if self.pause_reason else '')] if self.paused else [])
                        + (['زمان تیک از ساعت دستگاه جلوتر است؛ همگام‌سازی ساعت را بررسی کنید'] if any(m.get('tick',{}).get('time',0)>now+2 for m in self.markets.values()) else []) + (['اجرای بروکر فعال نشده است'] if not self.demo_identity else [])
                        + ([] if news_status(self.store,now,self.config['news_buffer_minutes'],self.config.get('news_mode')=='automatic')['ok'] else ['اخبار معتبر نیست یا در محدودهٔ توقف خبر هستیم']) + self.demo_blockers(now)),
                supported_timeframes=list(TIMEFRAMES)))

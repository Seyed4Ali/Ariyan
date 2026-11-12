import math
import uuid
from datetime import datetime, timezone
from .risk import size_for_risk


class PaperBroker:
    def __init__(self, store, config, stream='mt5-paper'):
        self.store, self.config, self.stream = store, config, stream

    def metrics(self, now):
        trades = self.store.trades(self.stream)
        closed = sorted((t for t in trades if t['status'] != 'OPEN'), key=lambda t: t['closed'])
        opened = [t for t in trades if t['status'] == 'OPEN']
        initial = self.store.get(f'{self.stream}:initial', self.config['starting_balance'])
        balance = initial + sum(t['pnl'] for t in closed)
        equity = balance + sum(t.get('floating', 0) for t in opened)
        day = datetime.fromtimestamp(now, timezone.utc).date()
        today = lambda ts: datetime.fromtimestamp(ts, timezone.utc).date() == day
        start_day = initial + sum(t['pnl'] for t in closed if not today(t['closed']))
        key = f'{self.stream}:peak'
        peak = max(self.store.get(key, initial), equity)
        self.store.set(key, peak)
        losses = 0
        for t in reversed(closed):
            # Daily reset of streak; loss streak cannot freeze the bot forever.
            if t['pnl'] >= 0 or not today(t['closed']):
                break
            losses += 1
        winners = sum(t['pnl'] > 0 for t in closed)
        loss_sum = -sum(t['pnl'] for t in closed if t['pnl'] < 0)
        return dict(initial=initial, balance=balance, equity=equity, peak=peak,
                    floating=sum(t.get('floating', 0) for t in opened), pnl=balance - initial,
                    drawdown_percent=max(0, (peak - equity) / peak * 100),
                    daily_drawdown_percent=max(0, (start_day - equity) / start_day * 100) if start_day > 0 else 100,
                    daily_pnl=equity - start_day, consecutive_losses=losses,
                    trades_today=sum(today(t['opened']) for t in trades),
                    last_exit=max((t['closed'] for t in closed), default=0),
                    open_count=len(opened), closed_count=len(closed),
                    win_rate=100 * winners / len(closed) if closed else None,
                    expectancy_r=sum(t['r'] for t in closed) / len(closed) if closed else None,
                    profit_factor=sum(t['pnl'] for t in closed if t['pnl'] > 0) / loss_sum if loss_sum else None)

    def open(self, candidate, tick, spec, now, policy='baseline', equity=None, persist=True, metrics=None):
        if self.store.seen(self.stream, candidate['cycle_id']):
            return None
        if any(t['status'] == 'OPEN' for t in self.store.trades(self.stream)):
            return None
        sign = 1 if candidate['direction'] == 'BUY' else -1
        slip = self.config['slippage_points'] * spec['point']
        entry = (tick['ask'] if sign == 1 else tick['bid']) + sign * slip
        distance = sign * (entry - candidate['sl'])
        min_stop = spec['stops_level'] * spec['point'] + (tick['ask'] - tick['bid'])
        if not .4 * candidate['atr'] <= distance <= 2.5 * candidate['atr'] or distance <= min_stop or spec['factor'] <= 0:
            return None
        # Snap all prices to broker tick size; round stop outward then recompute risk.
        step = spec['tick_size']
        if step <= 0:
            return None
        sl = (math.floor(candidate['sl'] / step) if sign == 1 else math.ceil(candidate['sl'] / step)) * step
        distance = abs(entry - sl)
        tp = entry + sign * distance * self.config['reward_risk']
        tp = (math.ceil(tp / step) if sign == 1 else math.floor(tp / step)) * step
        # Reserve commission and adverse exit slippage in risk budget.
        loss_per_lot = (distance + slip) * spec['factor']
        funds = equity if equity is not None else self.metrics(now)['equity']
        metrics = self.metrics(now) if metrics is None else metrics
        remaining_daily = max(0, self.config['daily_loss_percent'] - metrics['daily_drawdown_percent'])
        effective_risk = min(self.config['risk_percent'], remaining_daily)
        volume = size_for_risk(funds, effective_risk, loss_per_lot,
                              self.config['commission_per_lot_roundtrip_usd'],
                              spec['volume_min'], spec['volume_max'], spec['volume_step'])
        if not volume:
            return None
        risk = volume * (loss_per_lot + self.config['commission_per_lot_roundtrip_usd'])
        trade = dict(candidate, id=uuid.uuid4().hex[:12], stream=self.stream, policy=policy,
                     status='OPEN', opened=now, entry=entry, sl=round(sl, spec['digits']),
                     tp=round(tp, spec['digits']), volume=volume, factor=spec['factor'],
                     point=spec['point'], risk_money=risk, pnl=0, floating=0, r=0,
                     broker_symbol=spec['symbol'], commission=volume * self.config['commission_per_lot_roundtrip_usd'])
        if persist:
            self.store.save_trade(trade)
            self.store.event(now, 'PAPER_OPEN', dict(symbol=trade['symbol'], stream=self.stream, id=trade['id']))
        return trade

    def mark(self, symbol, tick, now, force=False):
        if not 0 <= now - tick['time'] <= self.config['max_tick_age_seconds']:
            return []
        closed = []
        for t in self.store.trades(self.stream):
            if t['status'] != 'OPEN' or t['symbol'] != symbol:
                continue
            sign = 1 if t['direction'] == 'BUY' else -1
            px = tick['bid'] if sign == 1 else tick['ask']
            stop = sign * (px - t['sl']) <= 0
            target = sign * (px - t['tp']) >= 0
            timed = now - t['opened'] >= self.config['max_hold_seconds']
            status = 'STOP' if stop else 'TARGET' if target else 'TIME' if timed else 'FLATTEN' if force else None
            # Gap-through stop fills at adverse observed price. TP capped at limit.
            exit_price = t['tp'] if target and not stop else px
            if status:
                exit_price -= sign * self.config['slippage_points'] * t['point']
            pnl = sign * (exit_price - t['entry']) * t['volume'] * t['factor'] - t['commission']
            t.update(current=px, floating=pnl)
            if status:
                t.update(status=status, closed=now, exit=exit_price, pnl=pnl, floating=0,
                         r=pnl / t['risk_money'])
                closed.append(t)
                self.store.event(now, 'PAPER_CLOSE', dict(symbol=symbol, status=status, pnl=pnl, stream=self.stream))
            self.store.save_trade(t)
        return closed

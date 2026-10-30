"""Auditable statistical checklist learner, with a frozen prospective test.

This is NOT an LLM and does not claim calibrated win probabilities. It can
only exclude one bad context in the simulated account; never change risk,
stops, execution permissions, or broker-demo policy.
"""
import math
import time
import uuid
from statistics import mean, stdev


def stats(rows):
    values = [r['r'] for r in rows]
    n = len(values)
    avg = mean(values) if n else 0
    # Conservative screening interval (not an independence guarantee).
    se = stdev(values) / math.sqrt(n) if n > 1 else float('inf')
    return dict(n=n, mean_r=avg, total_r=sum(values),
                upper=avg + 2.58 * se if n > 1 else None)


def matches(trade, rule):
    return trade.get('features', {}).get(rule['feature']) == rule['value']


class Learner:
    def __init__(self, store, config, state_key='learner'):
        self.store, self.config = store, config
        self.state_key = state_key
        self.state = store.get(state_key, dict(phase='collecting', active=None, candidate=None,
            cursor=0, version=0, message='برای ساخت فرضیه، ۱۰۰ معاملهٔ بسته‌شدهٔ واقعیِ شبیه‌سازی‌شده لازم است'))

    def save(self):
        self.store.set(self.state_key, self.state)

    def allow(self, features):
        active = self.state.get('active')
        return not active or features.get(active['rule']['feature']) != active['rule']['value']

    def rollback(self):
        self.state.update(active=None, phase='collecting', message='بازگشت به چک‌لیست پایه؛ سقف ریسک ثابت است')
        self.save()

    def update(self, baseline, paper, now=None):
        now = now or time.time()
        baseline = sorted((t for t in baseline if t['status'] != 'OPEN'), key=lambda t: t['closed'])
        active = self.state.get('active')
        if active:
            recent = [t for t in paper if t['status'] != 'OPEN' and t['opened'] > active['activated']][-30:]
            if len(recent) >= 30 and stats(recent)['mean_r'] < -.1:
                self.rollback()
                self.state['message'] = 'عملکرد ۳۰ معاملهٔ اخیر منفی شد؛ فیلتر خودکار برگشت'
                self.store.event(now, 'POLICY_ROLLBACK', dict(id=active['id']))
        candidate = self.state.get('candidate')
        if candidate:
            forward = [t for t in baseline if t['opened'] > candidate['cutoff']][:50]
            self.state.update(phase='forward', forward_count=len(forward))
            if len(forward) >= 50:
                excluded = [t for t in forward if matches(t, candidate['rule'])]
                kept = [t for t in forward if not matches(t, candidate['rule'])]
                ex, keep = stats(excluded), stats(kept)
                passed = ex['n'] >= 10 and ex['upper'] < 0 and keep['n'] >= 20 and keep['mean_r'] > 0
                candidate.update(forward_excluded=ex, forward_kept=keep, passed=passed)
                self.state.update(candidate=None, cursor=max(t['closed'] for t in forward))
                if passed and self.config['auto_apply_shadow_learning']:
                    candidate['activated'] = now
                    self.state.update(active=candidate, version=self.state['version'] + 1,
                                      message='فیلتر پس از ۵۰ معاملهٔ آینده فقط در حساب شبیه‌سازی اعمال شد')
                    self.store.event(now, 'POLICY_APPLIED_PAPER_ONLY', candidate)
                else:
                    self.state['message'] = 'فرضیه در آزمون آینده تأیید نشد؛ چک‌لیست پایه ادامه دارد'
                self.store.policy(candidate)
                self.state['phase'] = 'collecting'
            self.save()
            return self.snapshot(baseline)
        rows = [t for t in baseline if t['opened'] > self.state['cursor']]
        self.state['collection_count'] = len(rows)
        if len(rows) >= 100:
            rows = rows[:100]
            train = rows[:70]
            # Purge any position overlapping the training boundary.
            boundary = max(t['closed'] for t in train)
            validation = [t for t in rows[70:] if t['opened'] > boundary]
            options = []
            for feature in ('symbol', 'session', 'spread_bucket'):
                values = sorted({t.get('features', {}).get(feature, '') for t in train})
                for value in values:
                    rule = dict(feature=feature, value=value)
                    excluded = [t for t in train if matches(t, rule)]
                    score = stats(excluded)
                    if score['n'] >= 20 and score['upper'] is not None and score['upper'] < -.05:
                        options.append((score['mean_r'], rule, score))
            # Candidate selection uses training data ONLY. Validation is read once.
            selected = min(options, key=lambda x: x[0]) if options else None
            self.state['cursor'] = max(t['closed'] for t in rows)
            if selected:
                _, rule, train_score = selected
                ex = stats([t for t in validation if matches(t, rule)])
                kept = stats([t for t in validation if not matches(t, rule)])
                if ex['n'] >= 8 and ex['upper'] < 0 and kept['n'] >= 12 and kept['mean_r'] > 0:
                    candidate = dict(id=uuid.uuid4().hex[:10], created=now, cutoff=max(now, self.state['cursor']),
                                     rule=rule, training=train_score, validation_excluded=ex, validation_kept=kept,
                                     scope='paper-only', activated=None)
                    self.state.update(candidate=candidate, phase='forward', forward_count=0,
                        message='فرضیه قفل شد؛ در انتظار ۵۰ معاملهٔ بعدیِ دیده‌نشده در حساب مبنا')
                    self.store.policy(candidate)
                else:
                    self.state['message'] = 'اعتبارسنجی فرضیه کافی نبود؛ جمع‌آوری یک دستهٔ تازه آغاز شد'
            else:
                self.state['message'] = 'شواهد کافی برای تغییر قانون نیست؛ چک‌لیست پایه حفظ شد'
        self.save()
        return self.snapshot(baseline)

    def snapshot(self, baseline):
        return dict(self.state, total_trades=len(baseline), kind='statistical-checklist',
                    disclaimer='این ایجنت آماری است؛ تغییر فقط روی شبیه‌سازی. نتیجهٔ سودآوری تضمین‌شده نیست.')

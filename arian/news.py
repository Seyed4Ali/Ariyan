from datetime import datetime, timezone


def news_status(store, now, buffer_minutes=20, automatic=False):
    if automatic:
        cached = store.get('calendar_feed', {})
        fresh = bool(cached.get('fetched_at') and 0 <= now-cached['fetched_at'] <= 6*3600
                     and cached.get('week_start', 0) <= now < cached.get('week_end', 0))
        if not fresh:
            return dict(ok=False, reason='خوراک خودکار اخبار تازه یا مربوط به هفتهٔ جاری نیست', events=[], source='automatic')
        events = [e for e in cached.get('events', []) if e['currency'] in ('USD', 'EUR') and e.get('impact') == 'High']
        blocked = next((e for e in events if abs(e['time']-now) <= buffer_minutes*60), None)
        return dict(ok=blocked is None, reason='محدودهٔ توقف خبر: '+blocked['title'] if blocked else 'خوراک خودکار معتبر؛ خارج از بازهٔ خبر مهم',
                    events=events, source='automatic', valid_until=min(cached['week_end'],cached['fetched_at']+6*3600))

    calendar = store.get('news', {})
    checked = calendar.get('checked_at', 0)
    valid_until = calendar.get('valid_until', 0)
    if not checked <= now <= min(valid_until, checked + 24 * 3600):
        return dict(ok=False, reason='تقویم اخبار برای امروز بررسی نشده است', events=calendar.get('events', []))
    for event in calendar.get('events', []):
        if event['currency'] in ('USD', 'EUR') and abs(event['time'] - now) <= buffer_minutes * 60:
            return dict(ok=False, reason='محدودهٔ توقف خبر: ' + event['title'], events=calendar['events'])
    return dict(ok=True, reason='تأیید دستی تقویم معتبر است؛ فید خودکار اخبار متصل نیست',
                checked_at=checked, valid_until=valid_until, events=calendar.get('events', []))


def save_calendar(store, payload, now):
    valid_until = float(payload['valid_until'])
    if not now < valid_until <= now + 24 * 3600:
        raise ValueError('اعتبار تقویم باید حداکثر ۲۴ ساعت باشد')
    events = []
    for event in payload.get('events', []):
        ts = float(event['time'])
        if not now - 86400 < ts < now + 7 * 86400:
            raise ValueError('زمان رویداد باید نزدیک تاریخ جاری باشد')
        if event['currency'] not in ('USD', 'EUR'):
            raise ValueError('ارز خبر باید USD یا EUR باشد')
        events.append(dict(time=ts, title=str(event['title'])[:160], currency=event['currency']))
    if not payload.get('reviewed'):
        raise ValueError('تأیید بررسی تقویم الزامی است')
    calendar = dict(checked_at=now, valid_until=valid_until, events=events)
    store.set('news', calendar)
    store.event(now, 'NEWS_MANUAL_REVIEW', dict(events=len(events), valid_until=valid_until))
    return calendar

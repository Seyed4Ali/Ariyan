"""Public weekly calendar cache. Display/import only; never auto-approves news."""
import json
import threading
import time
import urllib.request
from datetime import datetime, timedelta

FEED_URL = 'https://nfs.faireconomy.media/ff_calendar_thisweek.json'


def parse_events(rows):
    if not isinstance(rows, list) or not rows or len(rows) > 2000:
        raise ValueError('ساختار خوراک تقویم معتبر نیست')
    events = []
    for row in rows:
        stamp = datetime.fromisoformat(row['date'])
        if stamp.tzinfo is None or row['impact'] not in ('High','Medium','Low','Holiday'):
            raise ValueError('زمان یا اهمیت خبر نامعتبر است')
        events.append(dict(time=stamp.timestamp(), title=str(row['title'])[:250],
            currency=str(row['country']), impact=row['impact'], forecast=str(row.get('forecast','')),
            previous=str(row.get('previous',''))))
    start = datetime.fromisoformat(rows[0]['date']).replace(hour=0,minute=0,second=0,microsecond=0)
    start -= timedelta(days=(start.weekday()+1)%7)
    end = start + timedelta(days=7)
    if any(not start.timestamp() <= e['time'] < end.timestamp()+3600 for e in events):
        raise ValueError('رویدادها در یک هفته قرار ندارند')
    return sorted(events,key=lambda e:e['time']), start.timestamp(), end.timestamp()


class CalendarFeed:
    def __init__(self, store, clock=None):
        self.store = store
        self.clock = clock or time.time
        self.lock = threading.Lock()
        self.refresh_requested = threading.Event()
        cached = self.store.get('calendar_feed',{})
        self.next_attempt = max(cached.get('fetched_at',0),cached.get('attempted_at',0)) + 3600 if cached else 0

    def refresh(self, now=None):
        now = now or self.clock()
        with self.lock:
            if now < self.next_attempt:
                return
            self.next_attempt = now + 3600  # provider exports update hourly; share one cached request
            try:
                request = urllib.request.Request(FEED_URL,headers={'Accept':'application/json','User-Agent':'Arian-Local-Calendar/1.0'})
                with urllib.request.urlopen(request,timeout=12) as response:
                    raw = response.read(1_000_001)
                if len(raw) > 1_000_000:
                    raise ValueError('پاسخ تقویم بیش از حد بزرگ است')
                events, start, end = parse_events(json.loads(raw))
                if not start <= now < end:
                    raise ValueError('خوراک تقویم مربوط به هفتهٔ جاری نیست')
                self.store.set('calendar_feed',dict(events=events,fetched_at=now,attempted_at=now,week_start=start,week_end=end,error=None))
            except Exception as exc:
                cached = self.store.get('calendar_feed',{})
                cached.update(error=str(exc), attempted_at=now)
                self.store.set('calendar_feed',cached)

    def snapshot(self, now=None):
        now = now or self.clock()
        cached = self.store.get('calendar_feed',{})
        fresh = bool(cached.get('fetched_at') and 0 <= now-cached['fetched_at'] <= 6*3600
                     and cached.get('week_start',0) <= now < cached.get('week_end',0))
        return dict(cached,source='Forex Factory · Fair Economy',source_url=FEED_URL,
            fresh=fresh,review_required=True,next_refresh=self.next_attempt)

    def loop(self, stop):
        while not stop.is_set():
            self.refresh()
            self.refresh_requested.wait(30)
            self.refresh_requested.clear()

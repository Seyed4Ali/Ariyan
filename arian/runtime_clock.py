"""Bounded UTC correction from two public HTTPS Date responses, never market ticks."""
import email.utils
import secrets
import threading
import time
import urllib.request

URLS = ('https://api.github.com/repos/shiyu-coder/Kronos',
        'https://api.github.com/repos/freqtrade/freqtrade')

class RuntimeClock:
    def __init__(self, mode='system'):
        if mode not in ('system','https-verified'):
            raise ValueError('Unknown runtime clock mode')
        self.mode=mode
        self.offset=0.
        self.synced_at=None
        self.anchor=None
        self.error=None
        self.lock=threading.Lock()

    def now(self):
        with self.lock:
            return self.anchor[0]+time.monotonic()-self.anchor[1] if self.anchor else time.time()

    def ready(self):
        with self.lock:
            return self.mode=='system' or bool(self.synced_at is not None and 0<=time.monotonic()-self.synced_at<=3600)

    def refresh(self):
        samples=[]
        try:
            for url in URLS:
                started=time.monotonic()
                request=urllib.request.Request(url+'?arian_clock_nonce='+secrets.token_hex(8),headers={'User-Agent':'Ariyan-UTC-Check/1.0','Cache-Control':'no-cache'})
                with urllib.request.urlopen(request,timeout=5) as response:
                    date=response.headers.get('Date')
                    age=float(response.headers.get('Age',0))
                    stamp=email.utils.parsedate_to_datetime(date)
                ended=time.monotonic()
                if stamp.tzinfo is None or ended-started>3 or age>0:
                    raise ValueError('پاسخ زمان کند یا کش‌شده است')
                # HTTP Date has one-second precision; use the midpoint of that second.
                samples.append((stamp.timestamp()+.5,ended))
            expected=samples[0][0]+samples[1][1]-samples[0][1]
            if abs(expected-samples[1][0])>2:
                raise ValueError('پاسخ‌های ساعت با یکدیگر سازگار نیستند')
            offset=samples[-1][0]-time.time()
            if abs(offset)>600:
                raise ValueError('اختلاف ساعت بیش از ده دقیقه است؛ ساعت ویندوز بررسی شود')
            with self.lock:
                self.anchor=samples[-1]
                self.offset=offset
                self.synced_at=time.monotonic()
                self.error=None
        except Exception as exc:
            with self.lock:self.error=str(exc)

    def snapshot(self):
        return dict(mode=self.mode,ready=self.ready(),offset_seconds=self.now()-time.time(),error=self.error)

    def loop(self,stop):
        if self.mode=='system':return
        while not stop.is_set():
            self.refresh()
            if stop.wait(300 if self.ready() else 15):return

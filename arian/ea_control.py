"""Account-scoped EA control. RUN is a short, renewable demo-only lease."""
import re
import uuid
import time
from pathlib import Path


def control_name(server, login, magic):
    server=re.sub('[^A-Za-z0-9_-]','_',server)
    return f'arian_control_{server}_{int(login)}_{int(magic)}.txt'


class EAControl:
    def __init__(self, store, magics, symbols=()):
        self.store,self.magics=store,magics
        self.symbols=set(symbols)
        self.error=None
        self.running=False
        self.run_id=None
        self.gate=dict(allowed=False,reasons=['شروع اکسپرت درخواست نشده است'])
        self.last_delivery=0
        if str(self.store.get('ea_stop_command','')).startswith('RUN:'):
            self.request('PAUSE')

    def bind(self, feed):
        if not feed.get('demo') or feed.get('currency')!='USD' or not feed.get('common_files_path'):
            return
        target=dict(server=feed['server'],login=feed['account_login'],directory=feed['common_files_path'])
        if target!=self.store.get('ea_control_target'):
            if self.running:
                self.request('PAUSE')
            self.store.set('ea_control_target',target)
        if not self.store.get('ea_stop_command') and self.store.get('paused',False):
            self.request('PAUSE')
        self.deliver()

    def request(self, action):
        if action not in ('PAUSE','FLATTEN'):
            raise ValueError('این پل فقط فرمان توقف می‌پذیرد')
        self.running=False
        self.store.set('ea_stop_command',action+':'+uuid.uuid4().hex)
        self.deliver()

    def start(self):
        self.running=True
        self.run_id=uuid.uuid4().hex
        self.last_delivery=0

    def renew(self, gate, risk_percent, now=None):
        self.gate=gate
        now=time.time() if now is None else now
        if not self.running or now-self.last_delivery<3:
            return
        # Reusing the command id makes chart acknowledgements meaningful across renewals.
        command=f'RUN:{self.run_id}|{int(now+20)}|{min(.25,max(0,risk_percent),gate.get("risk_budget",.25)):.6f}|{int(gate["allowed"])}'
        self.store.set('ea_stop_command',command)
        self.deliver()
        self.last_delivery=now

    def paths(self):
        target=self.store.get('ea_control_target')
        if not target:
            return []
        directory=Path(target['directory'])
        if not directory.is_absolute():
            raise ValueError('مسیر پل متاتریدر معتبر نیست')
        return [directory/control_name(target['server'],target['login'],magic) for magic in self.magics]

    def deliver(self):
        command=self.store.get('ea_stop_command')
        if not command:
            return
        try:
            for path in self.paths():
                path.parent.mkdir(parents=True,exist_ok=True)
                if path.exists() and path.read_text(encoding='ascii').strip()==command:
                    continue
                temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
                temporary.write_text(command+'\n',encoding='ascii')
                temporary.replace(path)
            self.error=None
        except (OSError,ValueError) as exc:
            self.error=str(exc)

    def snapshot(self):
        command=self.store.get('ea_stop_command')
        try:
            paths=self.paths()
            acknowledged=bool(command and paths and all(p.with_suffix(p.suffix+'.ack').exists() and
                p.with_suffix(p.suffix+'.ack').read_text(encoding='ascii').strip()==command for p in paths))
        except (OSError,ValueError):
            acknowledged=False
        charts=[]
        for path in self.paths():
            for status in path.parent.glob(path.name+'.*.status'):
                try:
                    fields=status.read_text(encoding='ascii').strip().split('|')
                    if len(fields) not in (8,9,10): continue
                    stamp=float(fields[7]);age=time.time()-stamp
                    if not 0<=age<=10: continue
                    charts.append(dict(symbol=fields[0],chart=fields[1],version=fields[2],
                        command=fields[3],state=fields[4],enabled=fields[5]=='1',audit_latched=fields[6]=='1',age=age,
                        timeframe=fields[8] if len(fields)>=9 else None,
                        entry_reason=fields[9] if len(fields)>=10 else None))
                except (OSError,ValueError): continue
        if command and (command.startswith('RUN:') or self.symbols):
            acknowledged=bool(charts and self.symbols.issubset({c['symbol'] for c in charts}) and all(c['command']==command.split('|')[0] for c in charts))
        return dict(requested=command.split(':')[0] if command else None,acknowledged=acknowledged,error=self.error,
                    running_requested=self.running,gate=self.gate,charts=charts,
                    target_known=bool(self.store.get('ea_control_target')),scope='configured-demo-ea')

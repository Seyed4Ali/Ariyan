import json
import sqlite3
from pathlib import Path
from contextlib import contextmanager


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS trades(
              id TEXT PRIMARY KEY, stream TEXT NOT NULL, cycle TEXT NOT NULL,
              status TEXT NOT NULL, opened REAL NOT NULL, closed REAL, payload TEXT NOT NULL,
              UNIQUE(stream,cycle));
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, ts REAL, kind TEXT, payload TEXT);
            CREATE TABLE IF NOT EXISTS policies(id TEXT PRIMARY KEY, ts REAL, payload TEXT);
            CREATE INDEX IF NOT EXISTS trade_stream ON trades(stream,status,closed);
            ''')

    @contextmanager
    def conn(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, key, default=None):
        with self.conn() as db:
            row = db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.conn() as db:
            db.execute('INSERT OR REPLACE INTO state VALUES(?,?)', (key, json.dumps(value, ensure_ascii=False)))

    def set_many(self, values):
        with self.conn() as db:
            db.executemany('INSERT OR REPLACE INTO state VALUES(?,?)',
                [(key,json.dumps(value,ensure_ascii=False)) for key,value in values.items()])

    def save_trade(self, trade):
        with self.conn() as db:
            db.execute('''INSERT INTO trades VALUES(?,?,?,?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET status=excluded.status,closed=excluded.closed,payload=excluded.payload''',
                       (trade['id'], trade['stream'], trade['cycle_id'], trade['status'], trade['opened'],
                        trade.get('closed'), json.dumps(trade, ensure_ascii=False)))

    def trades(self, stream=None, closed_only=False):
        sql, params, clauses = 'SELECT payload FROM trades', [], []
        if stream:
            clauses.append('stream=?'); params.append(stream)
        if closed_only:
            clauses.append("status<>'OPEN'")
        if clauses:
            sql += ' WHERE ' + ' AND '.join(clauses)
        sql += ' ORDER BY opened,id'
        with self.conn() as db:
            return [json.loads(r[0]) for r in db.execute(sql, params)]

    def seen(self, stream, cycle):
        with self.conn() as db:
            return db.execute('SELECT 1 FROM trades WHERE stream=? AND cycle=?', (stream, cycle)).fetchone() is not None

    def event(self, ts, kind, payload):
        with self.conn() as db:
            db.execute('INSERT INTO events(ts,kind,payload) VALUES(?,?,?)',
                       (ts, kind, json.dumps(payload, ensure_ascii=False)))

    def events(self, limit=40):
        with self.conn() as db:
            return [dict(ts=r['ts'], kind=r['kind'], payload=json.loads(r['payload']))
                    for r in db.execute('SELECT * FROM events ORDER BY id DESC LIMIT ?', (limit,))]

    def policy(self, policy):
        with self.conn() as db:
            db.execute('INSERT OR REPLACE INTO policies VALUES(?,?,?)',
                       (policy['id'], policy['created'], json.dumps(policy, ensure_ascii=False)))

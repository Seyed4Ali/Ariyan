"""Loopback-only dashboard and API; no third party services or cloud upload."""
import argparse
import csv
import io
import json
import mimetypes
import os
import secrets
import signal
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from arian.engine import Engine
from arian.market_tools import market_tools, WIDGET_ORIGIN

ROOT = Path(__file__).resolve().parent


class DashboardServer(ThreadingHTTPServer):
    """Bound parallel handlers so slow clients cannot exhaust host memory."""
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(4)
        super().__init__(*args, **kwargs)

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(5)
        return sock, address

    def process_request(self, request, client_address):
        self.slots.acquire()
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def make_handler(engine, token, port):
    hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
    origins = {'http://' + h for h in hosts}
    state_cache = dict(at=0, body=None)
    state_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, body, content_type='application/json; charset=utf-8', download=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
            elif isinstance(body, str):
                body = body.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-src " + WIDGET_ORIGIN + "; frame-ancestors 'none'")
            if download:
                self.send_header('Content-Disposition', f'attachment; filename="{download}"')
            self.end_headers()
            self.wfile.write(body)

        def allowed(self):
            if self.headers.get('Host') not in hosts:
                self.reply(403, dict(error='Loopback Host required'))
                return False
            return True

        def do_GET(self):
            if not self.allowed():
                return
            path = urlsplit(self.path).path
            if path == '/api/session':
                self.reply(200, dict(token=token))
            elif path == '/api/state':
                try:
                    with state_lock:
                        if state_cache['body'] is None or time.monotonic()-state_cache['at'] >= 1:
                            body = json.dumps(engine.snapshot(),ensure_ascii=False,allow_nan=False).encode('utf-8')
                            state_cache.update(at=time.monotonic(),body=body)
                        body = state_cache['body']
                    self.reply(200, body)
                except (MemoryError, ValueError):
                    self.reply(503, dict(error='ساخت وضعیت موقتاً ناموفق است؛ چند لحظه دیگر دوباره بررسی کنید'))
            elif path == '/api/market-tools':
                self.reply(200, market_tools())
            elif path == '/api/calendar':
                self.reply(200, engine.calendar_feed.snapshot())
            elif path == '/api/export':
                columns = ['id', 'stream', 'symbol', 'direction', 'status', 'opened', 'closed', 'entry', 'sl', 'tp', 'exit', 'volume', 'risk_money', 'pnl', 'r', 'policy']
                out = io.StringIO(newline='')
                writer = csv.DictWriter(out, fieldnames=columns, extrasaction='ignore')
                writer.writeheader()
                for t in engine.store.trades():
                    if '-plan' not in t['stream']:
                        writer.writerow(t)
                self.reply(200, '\ufeff' + out.getvalue(), 'text/csv; charset=utf-8', 'arian-trades.csv')
            elif path in ('/', '/app.js', '/style.css'):
                file = ROOT / 'web' / ('index.html' if path == '/' else path.lstrip('/'))
                self.reply(200, file.read_bytes(), mimetypes.guess_type(file.name)[0] + '; charset=utf-8')
            else:
                self.reply(404, dict(error='Not found'))

        def do_POST(self):
            if not self.allowed():
                return
            if self.headers.get('Origin') not in origins or not secrets.compare_digest(self.headers.get('X-Arian-Token', ''), token):
                self.reply(403, dict(error='Same-origin session required')); return
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                self.reply(415, dict(error='JSON required')); return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 65536:
                    raise ValueError('اندازهٔ درخواست نامعتبر است')
                payload = json.loads(self.rfile.read(size), parse_constant=lambda _: (_ for _ in ()).throw(ValueError('عدد نامعتبر')))
                path = urlsplit(self.path).path
                if path != '/api/control':
                    self.reply(404, dict(error='Not found')); return
                result = engine.action(payload['action'], payload)
                with state_lock:
                    state_cache['body'] = None
                self.reply(200, result)
            except (ValueError, TypeError, KeyError) as e:
                self.reply(400, dict(error=str(e)))

    return Handler


def main():
    parser = argparse.ArgumentParser(description='Arian local trading workbench')
    parser.add_argument('--port', type=int)
    parser.add_argument('--db', default=str(ROOT / 'data' / 'arian.sqlite'))
    args = parser.parse_args()
    config = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
    port = args.port or config['port']
    if config['host'] != '127.0.0.1':
        raise SystemExit('This build binds to 127.0.0.1 only.')
    if not 0 < config['risk_percent'] <= .25 or not 0 < config['daily_loss_percent'] <= 1 or not 0 < config['max_drawdown_percent'] <= 5:
        raise SystemExit('Risk caps: per trade <= .25%, daily <= 1%, drawdown <= 5%.')
    engine = Engine(config, args.db)
    # Bind before starting engine: a second launch cannot run another trade engine.
    try:
        server = DashboardServer(('127.0.0.1', port), make_handler(engine, secrets.token_urlsafe(32), port))
    except OSError:
        raise SystemExit(f'Port {port} is in use. Open http://127.0.0.1:{port} or stop that instance.')
    engine.start()
    pidfile = ROOT / 'data' / 'server.pid'
    pidfile.write_text(str(os.getpid()),encoding='ascii')
    print(f'Arian dashboard: http://127.0.0.1:{port}', flush=True)
    print('Real trading OFF. MT5 data -> local paper trading. Demo execution requires dashboard arming.', flush=True)
    try:
        server.serve_forever(poll_interval=.5)
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop.set()
        server.server_close()
        if pidfile.exists() and pidfile.read_text(encoding='ascii').strip() == str(os.getpid()):
            pidfile.unlink()


if __name__ == '__main__':
    main()

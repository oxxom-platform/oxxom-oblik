"""Loopback-only development service. Not a production accounting system."""
import argparse
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sqlite3
from uuid import uuid4

ROOT = Path(__file__).resolve().parent


def connect(path):
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    return db


def initialize(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS sales (
            id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY, document_id TEXT NOT NULL,
            action TEXT NOT NULL, created_at TEXT NOT NULL
        );
        PRAGMA user_version=1;
        ''')


def text_field(value, limit):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError('Перевірте обов’язкові поля та довжину тексту')
    return value.strip()


def number(value, lower, upper, places):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError('Некоректне число')
    try:
        result = Decimal(str(value))
        if not result.is_finite() or not lower <= result <= upper:
            raise ValueError('Число поза допустимими межами')
        if result != result.quantize(Decimal(places)):
            raise ValueError('Забагато знаків після коми')
        return result
    except InvalidOperation:
        raise ValueError('Некоректне число') from None


def validate_sale(data):
    if not isinstance(data, dict):
        raise ValueError('Очікується об’єкт документа')
    client = text_field(data.get('client'), 120)
    try:
        day = date.fromisoformat(data.get('date', ''))
    except (ValueError, TypeError):
        raise ValueError('Некоректна дата') from None
    raw_items = data.get('items')
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 100:
        raise ValueError('Документ має містити від 1 до 100 позицій')
    items, total = [], 0
    for raw in raw_items:
        if not isinstance(raw, dict) or raw.get('kind') not in ('Товар', 'Послуга'):
            raise ValueError('Некоректний тип позиції')
        price = number(raw.get('price'), 0, 100000000, '0.01')
        qty = number(raw.get('qty'), Decimal('0.001'), 10000, '0.001')
        cents = int((price * qty * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        items.append(dict(kind=raw['kind'], name=text_field(raw.get('name'), 200),
                          unit=text_field(raw.get('unit'), 20), price=float(price),
                          qty=float(qty), priceCents=int(price * 100), totalCents=cents))
        total += cents
    return dict(id='ЛК-' + uuid4().hex, client=client, date=day.strftime('%d.%m.%Y'),
                items=items, sum=total / 100, totalCents=total, status='Чернетка')


class Handler(BaseHTTPRequestHandler):
    def send(self, status, data, content_type='application/json; charset=utf-8'):
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def allowed(self):
        port = self.server.server_port
        return self.headers.get('Host') in (f'127.0.0.1:{port}', f'localhost:{port}')

    def do_GET(self):
        if not self.allowed():
            return self.send(403, {'error': 'Недозволений Host'})
        if self.path in ('/', '/index.html'):
            return self.send(200, (ROOT / 'app/index.html').read_bytes(), 'text/html; charset=utf-8')
        if self.path == '/api/health':
            return self.send(200, {'status': 'ok', 'mode': 'local-development', 'authentication': False})
        if self.path == '/api/session':
            return self.send(200, {'csrfToken': self.server.csrf})
        if self.path == '/api/sales':
            with connect(self.server.db_path) as db:
                sales = [json.loads(row['payload']) for row in db.execute('SELECT payload FROM sales ORDER BY rowid DESC')]
            return self.send(200, {'sales': sales})
        self.send(404, {'error': 'Не знайдено'})

    def do_POST(self):
        origin = self.headers.get('Origin')
        expected = 'http://' + self.headers.get('Host', '')
        if (not self.allowed() or (origin and origin != expected)
                or not secrets.compare_digest(self.headers.get('X-CSRF-Token', ''), self.server.csrf)):
            return self.send(403, {'error': 'Перезавантажте сторінку та повторіть спробу'})
        if self.path != '/api/sales':
            return self.send(404, {'error': 'Не знайдено'})
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.send(415, {'error': 'Потрібен application/json'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 131072:
                return self.send(413, {'error': 'Документ завеликий або порожній'})
            sale = validate_sale(json.loads(self.rfile.read(length)))
        except (ValueError, TypeError, UnicodeError) as exc:
            return self.send(400, {'error': str(exc)})
        try:
            now = datetime.now(timezone.utc).isoformat()
            with connect(self.server.db_path) as db:
                db.execute('INSERT INTO sales VALUES (?, ?, ?)', (sale['id'], json.dumps(sale, ensure_ascii=False), now))
                db.execute('INSERT INTO audit(document_id, action, created_at) VALUES (?, ?, ?)', (sale['id'], 'draft.created', now))
        except sqlite3.Error:
            return self.send(503, {'error': 'Не вдалося зберегти документ. Спробуйте ще раз'})
        self.send(201, sale)


def make_server(path, port=4173):
    initialize(path)
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.db_path = path
    server.csrf = secrets.token_urlsafe(32)
    return server


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=4173)
    parser.add_argument('--database', type=Path, default=ROOT / 'data/oblik.sqlite3')
    args = parser.parse_args()
    server = make_server(args.database, args.port)
    print(f'OXXOM Облік: http://127.0.0.1:{server.server_port} — local development, no authentication', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

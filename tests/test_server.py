import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from io import BytesIO
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from server import Handler, connect, initialize, validate_sale


def example(**changes):
    data = dict(client='Тестовий клієнт', date='2026-11-15', items=[
        dict(name='Налаштування', kind='Послуга', unit='год', price='1500.00', qty='2.5')])
    data.update(changes)
    return data


class SalesTests(unittest.TestCase):
    def request(self, path, method='GET', payload=None, token='test-token', host='127.0.0.1:4173'):
        body = json.dumps(payload).encode() if payload is not None else b''
        raw = (f'{method} {path} HTTP/1.0\r\nHost: {host}\r\n'
               f'Content-Type: application/json\r\nX-CSRF-Token: {token}\r\n'
               f'Content-Length: {len(body)}\r\n\r\n').encode() + body
        class Socket:
            output = b''
            def makefile(self, *args):
                return BytesIO(raw)
            def sendall(self, chunk):
                self.output += chunk
        sock = Socket()
        Handler(sock, ('127.0.0.1', 12345), self.http_server)
        header, body = sock.output.split(b'\r\n\r\n', 1)
        return int(header.split()[1]), body

    def test_api_create_read_and_protect(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'test.sqlite3'
            initialize(path)
            self.http_server = SimpleNamespace(server_port=4173, csrf='test-token', db_path=path)
            self.assertEqual(self.request('/api/sales', 'POST', example(), token='wrong')[0], 403)
            self.assertEqual(self.request('/api/session', host='attacker.example')[0], 403)
            self.assertEqual(self.request('/server.py')[0], 404)
            self.assertEqual(self.request('/api/sales', 'POST', example(items=[]))[0], 400)
            status, raw = self.request('/api/sales', 'POST', example())
            self.assertEqual(status, 201)
            sale = json.loads(raw)
            status, raw = self.request('/api/sales')
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(raw)['sales'], [sale])
            self.assertEqual(self.request('/')[0], 200)
            with connect(path) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM audit').fetchone()[0], 1)

    def test_server_ignores_forged_total_and_status(self):
        sale = validate_sale(example(sum=1, totalCents=1, status='Оплачено'))
        self.assertEqual(sale['totalCents'], 375000)
        self.assertEqual(sale['status'], 'Чернетка')

    def test_rounds_each_line_half_up(self):
        item = dict(name='Тест', kind='Товар', unit='кг', price='0.01', qty='0.5')
        self.assertEqual(validate_sale(example(items=[item, item]))['totalCents'], 2)

    def test_invalid_inputs(self):
        invalid = [example(client=' '), example(date='2026-02-30'), example(items=[]), None]
        for value in ('NaN', 'Infinity', '-1', '1.001', True, {}, '100000001'):
            data = example()
            data['items'][0]['price'] = value
            invalid.append(data)
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValueError):
                validate_sale(data)

    def test_database_persists_and_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'test.sqlite3'
            initialize(path)
            sale = validate_sale(example())
            with connect(path) as db:
                db.execute('INSERT INTO sales VALUES (?, ?, ?)', (sale['id'], json.dumps(sale), 'now'))
            initialize(path)
            with connect(path) as db:
                self.assertEqual(json.loads(db.execute('SELECT payload FROM sales').fetchone()[0])['totalCents'], 375000)
            with self.assertRaises(sqlite3.IntegrityError), connect(path) as db:
                db.execute("INSERT INTO audit(document_id, action, created_at) VALUES ('x', 'draft.created', 'now')")
                db.execute('INSERT INTO sales VALUES (?, ?, ?)', (sale['id'], '{}', 'now'))
            with connect(path) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM audit').fetchone()[0], 0)

    def test_concurrent_documents_have_distinct_ids(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(lambda _: validate_sale(example())['id'], range(100)))
        self.assertEqual(len(set(ids)), 100)


if __name__ == '__main__':
    unittest.main()

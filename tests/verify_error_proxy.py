"""Real Nginx contract: original statuses, JSON, languages and upstream failure."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
from threading import Thread
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class Application(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(503)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"error":"application_unavailable"}')

    def log_message(self, *args):
        pass


def check(port, path, status, language='uk', method='GET', cookie=''):
    req = Request(f'http://127.0.0.1:{port}{path}', method=method, headers={'Cookie': cookie})
    try:
        response = urlopen(req, timeout=3)
    except HTTPError as error:
        response = error
    with response:
        body = response.read()
        assert response.status == status, (path, method, response.status, status)
        if path.startswith('/api/'):
            assert response.headers['Content-Type'].startswith('application/json'), response.headers
            assert 'error' in json.loads(body)
        elif path != '/__oblik_errors/503.html':
            assert response.headers['Content-Type'].startswith('text/html'), response.headers
            assert f'lang="{language}"'.encode() in body
            assert f'HTTP {status}'.encode() in body
            assert response.headers['Content-Language'] == language
            assert response.headers['X-Robots-Tag'] == 'noindex, nofollow'
            assert "default-src 'none'" in response.headers['Content-Security-Policy']


def main():
    nginx = shutil.which('nginx')
    if not nginx:
        raise SystemExit('Nginx required: this check must not silently skip')
    pages = Path(sys.argv[1]).resolve()
    application = ThreadingHTTPServer(('127.0.0.1', 0), Application)
    Thread(target=application.serve_forever, daemon=True).start()
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='oblik-nginx-') as name:
        root = Path(name)
        include = (pages / 'oblik-errors.conf').read_text().replace('/data/oxxom-oblik-errors', str(pages))
        (root / 'errors.conf').write_text(include)
        locations = ''.join(f'location = /{prefix}test/{status} {{ return {status}; }}\n'
                            for prefix in ('', 'api/', 'en/', 'ru/', 'cs/') for status in (429, 502, 503, 504))
        (root / 'nginx.conf').write_text(f'''
daemon off;
pid {root}/nginx.pid;
error_log {root}/error.log warn;
events {{ worker_connections 64; }}
http {{
    access_log off;
    proxy_temp_path {root}/proxy;
    client_body_temp_path {root}/body;
    server {{
        listen 127.0.0.1:{port};
        server_name oblik.example;
        include {root}/errors.conf;
        {locations}
        location /api/app {{ proxy_pass http://127.0.0.1:{application.server_port}; proxy_intercept_errors off; }}
        location /down {{ proxy_pass http://127.0.0.1:1; }}
        location / {{ return 404; }}
    }}
}}
''')
        subprocess.run([nginx, '-p', str(root), '-c', str(root / 'nginx.conf'), '-t'], check=True, timeout=10)
        process = subprocess.Popen([nginx, '-p', str(root), '-c', str(root / 'nginx.conf')])
        try:
            for attempt in range(30):
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.2):
                        break
                except OSError:
                    time.sleep(.1)
            count = 0
            for status in (429, 502, 503, 504):
                for language in ('uk', 'en', 'ru', 'cs'):
                    prefix = '' if language == 'uk' else language + '/'
                    check(port, f'/{prefix}test/{status}', status, language)
                    check(port, f'/test/{status}', status, language, cookie='oxxom_language=' + language)
                    count += 2
                for method in ('GET', 'POST'):
                    check(port, f'/test/{status}', status, method=method)
                    check(port, f'/api/test/{status}', status, method=method)
                    count += 2
            check(port, '/en/test/503', 503, 'en', cookie='oxxom_language=ru')
            check(port, '/test/503', 503, 'uk', cookie='oxxom_language=../ru')
            check(port, '/down', 502)
            check(port, '/__oblik_errors/503.html', 404)
            req = urlopen  # Application JSON must pass through without interception.
            try:
                req(f'http://127.0.0.1:{port}/api/app', timeout=3)
            except HTTPError as error:
                assert error.code == 503
                assert json.loads(error.read()) == {'error': 'application_unavailable'}
            else:
                raise AssertionError('Application status was rewritten')
            print(f'NGINX ERROR PAGES OK: {count + 5} real HTTP assertions')
        finally:
            process.terminate()
            process.wait(timeout=5)
            application.shutdown()


if __name__ == '__main__':
    main()

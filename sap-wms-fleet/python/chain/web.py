import json
import re
import threading
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

# 服务之间只访问本机地址，系统代理会把这些请求转走，所以不读代理设置。
_opener = build_opener(ProxyHandler({}))
_print_lock = threading.Lock()


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(f'{status} {message}')
        self.status = status
        self.message = message


class Server(ThreadingHTTPServer):
    daemon_threads = True

    @property
    def url(self):
        return f'http://127.0.0.1:{self.server_address[1]}'

    def stop(self):
        self.shutdown()
        self.server_close()


def serve(routes, port=0, host='127.0.0.1'):
    server = Server((host, port), _handler(routes))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def call(method, url, body=None, headers=None):
    data = None if body is None else json.dumps(body).encode('utf-8')
    request = Request(url, data=data, method=method,
                      headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        with _opener.open(request, timeout=5) as response:
            return _decode(response.read()), response.headers
    except HTTPError as error:
        with error:
            payload = _decode(error.read())
        message = payload.get('error') if isinstance(payload, dict) else payload
        raise HttpError(error.code, message) from None


def find(items, key, kind):
    if key not in items:
        raise HttpError(404, f'{kind} {key} 不存在')
    return items[key]


def log(system, message):
    stamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
    with _print_lock:
        print(f'{stamp} [{system}] {message}', flush=True)


def _decode(raw):
    return json.loads(raw) if raw else None


def _handler(routes):
    table = [(method, re.compile(pattern), action) for method, pattern, action in routes]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self._dispatch('GET')

        def do_POST(self):
            self._dispatch('POST')

        def _dispatch(self, method):
            url = urlsplit(self.path)
            length = int(self.headers.get('Content-Length') or 0)
            request = SimpleNamespace(
                query={key: values[0] for key, values in parse_qs(url.query).items()},
                body=json.loads(self.rfile.read(length)) if length else {},
                headers=self.headers,
            )
            try:
                result = self._route(method, url.path, request)
            except HttpError as error:
                result = (error.status, {'error': error.message})
            except Exception as error:
                traceback.print_exc()
                result = (500, {'error': repr(error)})
            if not isinstance(result, tuple):
                result = (200, result)
            status, payload, headers = (result + ({},))[:3]
            data = json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def _route(self, method, path, request):
            for verb, pattern, action in table:
                match = pattern.fullmatch(path)
                if verb == method and match:
                    return action(request, *match.groups())
            raise HttpError(404, f'没有这个地址：{method} {path}')

        def log_message(self, format, *args):
            pass

    return Handler

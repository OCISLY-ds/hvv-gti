import base64
import gzip
import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Server:
    def __init__(self, handler_cls):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        self.httpd.owner = self
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


class _GtiHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        owner = self.server.owner
        body = self.rfile.read(int(self.headers["Content-Length"]))
        owner.requests.append((self.path, json.loads(body), dict(self.headers)))
        expected = base64.b64encode(hmac.new(owner.password.encode(), body, hashlib.sha1).digest()).decode()
        if (
            self.headers.get("geofox-auth-user") != owner.user
            or self.headers.get("geofox-auth-type") != "HmacSHA1"
            or self.headers.get("geofox-auth-signature") != expected
        ):
            self._send(401, b"Authentication failed!", "text/plain")
            return
        method = self.path.rsplit("/", 1)[-1]
        response = owner.responses.get(method, {"returnCode": "OK"})
        if callable(response):
            response = response(json.loads(body))
        self._send(200, json.dumps(response, ensure_ascii=False).encode("utf-8"), "application/json", owner.gzip)

    def _send(self, status, payload, content_type, compress=False):
        if compress:
            payload = gzip.compress(payload)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if compress:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class FakeGti(_Server):
    def __init__(self, user="tester", password="geheim", responses=None, gzip_responses=False):
        super().__init__(_GtiHandler)
        self.user, self.password = user, password
        self.responses = responses or {}
        self.gzip = gzip_responses
        self.requests: list = []


class _InfluxHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        owner = self.server.owner
        body = self.rfile.read(int(self.headers["Content-Length"]))
        owner.writes.append((self.path, self.headers.get("Authorization"), body.decode("utf-8")))
        status = owner.status
        self.send_response(status)
        self.send_header("Content-Length", "0" if status == 204 else "4")
        self.end_headers()
        if status != 204:
            self.wfile.write(b"nope")


class FakeInflux(_Server):
    def __init__(self, status=204):
        super().__init__(_InfluxHandler)
        self.status = status
        self.writes: list = []

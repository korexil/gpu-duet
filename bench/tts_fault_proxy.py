"""A fault-injecting proxy in front of tts-service, for the interrupt fixture.

  python bench/tts_fault_proxy.py --upstream http://127.0.0.1:18766 --port 18767

Forwards GET /health and POST /stream unchanged. POST /fault arms the next requests:

  {"mode": "error"}                 -> answer 500 before any audio
  {"mode": "hang"}                  -> accept the request, send nothing, keep the connection open
  {"mode": "hang", "after_s": 1.0}  -> forward audio for after_s seconds, then send nothing more
  {"mode": "none"}                  -> clear

"count" (default 1) is how many /stream requests the fault applies to. GET /fault shows the
current setting. A hung connection is released when the client goes away or after --hang-max-s.

Not a strict streaming proxy: upstream audio is forwarded in 4800-byte reads (100 ms), and the
"hang after_s" fault is checked between reads, so it only triggers while audio is still flowing.
"""
import argparse
import json
import select
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def make_server(upstream, port, hang_max_s):
    state = dict(mode='none', count=0, after_s=0.0)
    lock = threading.Lock()

    def take_fault():
        with lock:
            if state['mode'] == 'none' or state['count'] <= 0:
                return None
            state['count'] -= 1
            fault = dict(state)
            if state['count'] == 0:
                state['mode'] = 'none'
            return fault

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, fmt, *args):
            pass

        def client_closed(self):
            return bool(select.select([self.connection], [], [], 0)[0]) and not self.connection.recv(1, socket.MSG_PEEK)

        def hold(self):
            deadline = time.monotonic() + hang_max_s
            while time.monotonic() < deadline and not self.client_closed():
                time.sleep(0.1)
            self.close_connection = True

        def reply_json(self, code, obj):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == '/fault':
                with lock:
                    return self.reply_json(200, state)
            try:
                with urllib.request.urlopen(upstream + self.path, timeout=5) as r:
                    data = r.read()
                    self.send_response(r.status)
                    self.send_header('Content-Type', r.headers.get('Content-Type', 'application/json'))
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
            except urllib.error.HTTPError as error:
                self.send_error(error.code)
            except Exception:
                self.send_error(502, 'upstream unreachable')

        def do_POST(self):
            size = int(self.headers.get('Content-Length') or 0)
            body = self.rfile.read(size)
            if self.path == '/fault':
                try:
                    req = json.loads(body)
                    mode = req.get('mode', 'none')
                    assert mode in ('none', 'error', 'hang')
                    with lock:
                        state.update(mode=mode, count=int(req.get('count', 1)) if mode != 'none' else 0,
                                     after_s=float(req.get('after_s', 0.0)))
                        return self.reply_json(200, state)
                except Exception:
                    return self.send_error(400, 'bad fault request')
            if self.path != '/stream':
                return self.send_error(404)
            fault = take_fault()
            if fault and fault['mode'] == 'error':
                return self.send_error(500, 'injected failure')
            if fault and fault['mode'] == 'hang' and fault['after_s'] <= 0:
                return self.hold()
            request = urllib.request.Request(upstream + '/stream', data=body,
                                             headers={'Content-Type': 'application/json'}, method='POST')
            started = time.monotonic()
            try:
                with urllib.request.urlopen(request, timeout=60) as r:
                    self.send_response(r.status)
                    self.send_header('Content-Type', r.headers.get('Content-Type', 'audio/pcm'))
                    self.send_header('Transfer-Encoding', 'chunked')
                    self.end_headers()
                    while True:
                        if fault and fault['mode'] == 'hang' and time.monotonic() - started >= fault['after_s']:
                            return self.hold()           # the upstream synthesis is dropped with the request
                        data = r.read(4800)
                        if not data:
                            break
                        self.wfile.write(f'{len(data):X}\r\n'.encode() + data + b'\r\n')
                        self.wfile.flush()
                    self.wfile.write(b'0\r\n\r\n')
                    self.wfile.flush()
            except urllib.error.HTTPError as error:
                self.send_error(error.code)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                self.close_connection = True
            except Exception:
                self.send_error(502, 'upstream failed')

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--upstream', default='http://127.0.0.1:18766')
    parser.add_argument('--port', type=int, default=18767)
    parser.add_argument('--hang-max-s', type=float, default=120.0)
    args = parser.parse_args()
    server = make_server(args.upstream.rstrip('/'), args.port, args.hang_max_s)
    print(f'tts fault proxy on http://127.0.0.1:{args.port} -> {args.upstream}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()

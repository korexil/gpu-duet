"""tts-service HTTP behaviour with a fake engine: queueing, counters, dropped clients."""
import json
import socket
import threading
import time
import urllib.request

import numpy as np
import pytest

from leveler import Leveler
from server import make_server


class FakeTTS:
    backend = active_backend = 'fake'

    def __init__(self, seconds=0.3):
        self.seconds = seconds
        self.calls = []

    def synthesize(self, text, emit, tail=True):
        self.calls.append(text)
        wave = (0.1 * np.sin(np.arange(24000 * 1.0) / 24000 * 2 * np.pi * 220)).astype(np.float32)
        emit(wave)
        time.sleep(self.seconds)
        emit(wave)
        return dict(success=True, cancelled=False, output_samples=len(wave) * 2, wall_seconds=self.seconds,
                    token_budget=128, truncated=False, seed=7, frames=12)


@pytest.fixture
def service():
    tts = FakeTTS()
    server = make_server(tts, Leveler(sentence_pause=0.2), 0, queue_timeout=2.0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield tts, f'http://127.0.0.1:{port}', port
    server.shutdown()
    server.server_close()


def post(base, path, text, timeout=10):
    req = urllib.request.Request(f'{base}{path}', data=json.dumps({'text': text}).encode(),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def health(base):
    with urllib.request.urlopen(f'{base}/health', timeout=5) as r:
        return json.loads(r.read())


def test_stream_and_counters(service):
    tts, base, _ = service
    status, body = post(base, '/stream', '今日はいい天気ですね。')
    assert status == 200 and len(body) > 24000
    assert health(base)['started'] == health(base)['served'] == 1


def test_second_request_queues_instead_of_503(service):
    tts, base, _ = service
    results = {}

    def go(k):
        results[k] = post(base, '/synthesize', k)
    threads = [threading.Thread(target=go, args=(k,)) for k in ('a', 'b')]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert all(r[0] == 200 for r in results.values())
    assert sorted(tts.calls) == ['a', 'b']
    assert health(base)['served'] == 2


def test_client_that_left_while_queued_is_never_synthesized(service):
    tts, base, port = service
    first = threading.Thread(target=post, args=(base, '/synthesize', 'first'))
    first.start()
    time.sleep(0.05)
    body = json.dumps({'text': 'abandoned'}).encode()
    s = socket.create_connection(('127.0.0.1', port))
    s.sendall(b'POST /stream HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n'
              + f'Content-Length: {len(body)}\r\n\r\n'.encode() + body)
    time.sleep(0.05)
    s.close()  # the user interrupted before the engine was free
    first.join()
    time.sleep(0.3)
    assert tts.calls == ['first']
    assert health(base)['started'] == 1 and not health(base)['busy']

"""Live view of what the server is doing: an event bus the sessions publish to, a system-metrics
sampler (RAM, VRAM, GPU load) and the server-sent-events endpoint the dashboard page listens on.

  http://<host>:<http_port>/dashboard   the page (standalone/dashboard.html)
  http://<host>:<http_port>/events      text/event-stream of JSON lines
"""
import ctypes
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

HTML = Path(__file__).resolve().parent / 'dashboard.html'


class Bus:
    """Fan-out of small JSON events to every open /events connection. Thread-safe."""

    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers = []
        self.last_metrics = None
        self.replay = []           # the current answer so far, so a page opened mid-answer catches up

    def publish(self, event):
        event = {**event, 't': time.time()}
        kind = event.get('type')
        if kind == 'metrics':
            self.last_metrics = event
        elif kind == 'question':
            self.replay = [event]
        elif self.replay and len(self.replay) < 20000:
            self.replay.append(event)
        with self._lock:
            for q in self._subscribers:
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass

    def subscribe(self):
        q = queue.Queue(maxsize=40000)
        with self._lock:
            self._subscribers.append(q)
            for event in ([self.last_metrics] if self.last_metrics else []) + self.replay:
                q.put_nowait(event)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)


def ram_gb():
    """(used, total) in GB without psutil."""
    if sys.platform == 'win32':
        class Status(ctypes.Structure):
            _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                        ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                        ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                        ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                        ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
        status = Status()
        status.dwLength = ctypes.sizeof(Status)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        total, avail = status.ullTotalPhys, status.ullAvailPhys
    else:
        info = {}
        for line in Path('/proc/meminfo').read_text().splitlines():
            key, _, rest = line.partition(':')
            info[key] = int(rest.split()[0]) * 1024
        total, avail = info['MemTotal'], info['MemAvailable']
    return round((total - avail) / 2 ** 30, 2), round(total / 2 ** 30, 2)


def gpu_stats():
    """(vram used MiB, vram total MiB, utilisation %) from nvidia-smi, or None."""
    try:
        out = subprocess.run(['nvidia-smi', '--query-gpu=memory.used,memory.total,utilization.gpu',
                              '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=5,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
        used, total, util = (int(x) for x in out.strip().splitlines()[0].split(','))
        return used, total, util
    except Exception:
        return None


def start_metrics(bus, mode, title=None, interval=1.0):
    def loop():
        while True:
            used, total = ram_gb()
            event = {'type': 'metrics', 'mode': mode, 'title': title, 'ram_used_gb': used, 'ram_total_gb': total}
            if gpu := gpu_stats():
                event.update(vram_used_mb=gpu[0], vram_total_mb=gpu[1], gpu_util=gpu[2])
            bus.publish(event)
            time.sleep(interval)
    threading.Thread(target=loop, daemon=True, name='metrics').start()


def serve_events(handler, bus):
    """Keep one HTTP response open and stream bus events as SSE until the client goes away."""
    handler.send_response(200)
    handler.send_header('Content-Type', 'text/event-stream; charset=utf-8')
    handler.send_header('Cache-Control', 'no-cache')
    handler.send_header('Connection', 'close')
    handler.end_headers()
    q = bus.subscribe()
    try:
        while True:
            try:
                event = q.get(timeout=15)
                handler.wfile.write(f'data: {json.dumps(event, ensure_ascii=False)}\n\n'.encode('utf-8'))
            except queue.Empty:
                handler.wfile.write(b': ping\n\n')
            handler.wfile.flush()
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
        pass
    finally:
        bus.unsubscribe(q)


def serve_page(handler):
    data = HTML.read_bytes()
    handler.send_response(200)
    handler.send_header('Content-Type', 'text/html; charset=utf-8')
    handler.send_header('Content-Length', str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)

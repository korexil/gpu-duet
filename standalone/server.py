"""gpu-duet standalone server: one process between a xiaozhi-firmware device and the local
llama-server + tts-service. No Docker, no database, no management console.

  python standalone/server.py --config standalone/config.yaml

Two ports, same split as xiaozhi-esp32-server: the websocket the device talks on
(``ws://host:8000/xiaozhi/v1/``) and a plain HTTP port (8003) for ``/xiaozhi/ota/``, which a
device whose OTA URL points here fetches at boot to learn the websocket address, plus ``/``
for a status page.
"""
import argparse
import asyncio
import copy
import json
import logging
import socket
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import yaml  # noqa: E402
from websockets.asyncio.server import serve  # noqa: E402

from asr import Denoiser, Recognizer, Vad  # noqa: E402
import dashboard  # noqa: E402
from brain import Brain  # noqa: E402
from session import Session  # noqa: E402
from tts_client import TtsClient  # noqa: E402

log = logging.getLogger('standalone')

DEFAULTS = {
    'listen': {'host': '0.0.0.0', 'port': 8000, 'http_port': 8003},
    'advertise_host': '',         # host in the OTA answer; empty = the Host the device asked
    'advertise_port': 0,          # websocket port in the OTA answer, when a proxy sits in front; 0 = listen.port
    'ws_path': '/xiaozhi/v1/',
    'token': '',
    'llm': {'base_url': 'http://127.0.0.1:18080', 'max_tokens': 1500, 'temperature': 0.8, 'top_p': 0.95,
            'top_k': 64, 'chunk_tokens': 16, 'turn_tokens': 48, 'handoff_wait': 0.4, 'turn_taking': False,
            'question_rest_note': ''},
    'tts': {'base_url': 'http://127.0.0.1:18766', 'sample_rate': 24000},
    'asr': {'model_dir': 'models/sense-voice', 'vad_model': 'models/silero_vad.onnx', 'language': 'zh',
            'threads': 2, 'vad_threshold': 0.5, 'vad_threshold_low': 0.2,
            'denoise_model': 'models/gtcrn_simple.onnx', 'denoise': True},
    'prompt': ('你是一个在本地电脑上运行的语音助手。平时聊天回答口语化、简短，两三句话说完；对方让你讲故事、讲解或要求详细时，'
               '一次讲完整，不要中途停下来问要不要继续。不用列表、标题和表情符号，全部用能念出来的自然句子。'),
    'history_turns': 10,
    'splitter': {'min_chars': 30, 'max_chars': 70, 'first_min_chars': 12, 'first_cut_chars': 0},
    'wake': {'words': ['你好小智', '小智小智', '嘿你好'], 'ack': '嗯？', 'device_text': 'chat'},
    'voice': {'end_silence_ms': 700, 'min_voice_ms': 250, 'preroll_ms': 600, 'max_utterance_s': 20,
              'guard_ms': 500, 'barge_in': False, 'barge_in_ms': 600, 'chunk_pause_ms': 400, 'tail_hold_ms': 1200,
              'agc': {'enabled': True, 'target_db': -20.0, 'attack_db_s': 60.0, 'release_db_s': 6.0, 'max_boost_db': 8.0, 'max_cut_db': 10.0}},
    'idle_close_s': 0,            # 0 = stay connected and keep listening; >0 = close after that many silent seconds
    'ws_ping_interval': 20,
    'ws_ping_timeout': 60,
}


def merge(base, override):
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        out[key] = merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def load_config(path):
    path = Path(path)
    with path.open(encoding='utf-8') as stream:
        cfg = merge(DEFAULTS, yaml.safe_load(stream) or {})
    for key in ('model_dir', 'vad_model', 'denoise_model'):
        value = Path(cfg['asr'][key])
        cfg['asr'][key] = str(value if value.is_absolute() else (path.parent / value).resolve())
    return cfg


def lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('8.8.8.8', 80))
            return s.getsockname()[0]
    except OSError:
        return '127.0.0.1'


class Context:
    """Shared by all sessions: config, models, clients, and the pre-encoded wake acknowledgement."""

    def __init__(self, cfg, codec_factory=None, vad_factory=None, asr=None, brain=None, tts=None, wake_ack=None,
                 denoiser=None):
        self.cfg = cfg
        self.out_rate = int(cfg['tts']['sample_rate'])
        self.make_codec = codec_factory
        self.make_vad = vad_factory
        self.asr, self.brain, self.tts, self.denoiser = asr, brain, tts, denoiser
        self.wake_ack = wake_ack or ([], '')
        self.bus = dashboard.Bus()       # live events for /dashboard
        self.sessions = set()
        self.started_at = time.time()
        self.http_port = None
        self.ws_port = None

    @classmethod
    def build(cls, cfg):
        from opus_codec import OpusCodec, load_library
        lib = load_library()
        out_rate = int(cfg['tts']['sample_rate'])
        asr_cfg = cfg['asr']
        log.info('loading VAD %s', asr_cfg['vad_model'])

        def vad_factory():
            return Vad(asr_cfg['vad_model'], asr_cfg['vad_threshold'], asr_cfg['vad_threshold_low'])
        vad_factory()  # fail early if the model is missing
        log.info('loading SenseVoice %s (language=%s)', asr_cfg['model_dir'], asr_cfg['language'])
        asr = Recognizer(asr_cfg['model_dir'], asr_cfg['language'], asr_cfg['threads'])
        denoiser = None
        if asr_cfg.get('denoise') and Path(asr_cfg['denoise_model']).is_file():
            denoiser = Denoiser(asr_cfg['denoise_model'])
            log.info('denoiser %s loaded', Path(asr_cfg['denoise_model']).name)
        elif asr_cfg.get('denoise'):
            log.warning('denoise model %s missing (run setup_models.py); ASR gets the raw microphone', asr_cfg['denoise_model'])
        tts = TtsClient(cfg['tts']['base_url'], out_rate)
        brain = Brain(cfg['llm'], tts_health_url=cfg['tts']['base_url'].rstrip('/') + '/health')
        ctx = cls(cfg, lambda: OpusCodec(Session.IN_RATE, out_rate, lib), vad_factory, asr, brain, tts, denoiser=denoiser)
        ctx.wake_ack = ctx.prepare_wake_ack(lib)
        dashboard.start_metrics(ctx.bus, cfg.get('dashboard_mode') or ('overlap' if cfg['llm'].get('overlap') else 'duet'),
                                cfg.get('dashboard_title'))
        return ctx

    def prepare_wake_ack(self, lib):
        from opus_codec import Encoder
        text = (self.cfg['wake'].get('ack') or '').strip()
        if not text:
            return [], ''
        if not self.tts.health():
            log.warning('TTS at %s not reachable, wake acknowledgement disabled', self.tts.base_url)
            return [], ''
        try:
            pcm = self.tts.synthesize(text)
        except Exception as error:
            log.warning('wake acknowledgement synthesis failed: %s', error)
            return [], ''
        frame = self.out_rate * Session.FRAME_MS // 1000
        size = frame * 2
        pcm += bytes((-len(pcm)) % size)
        encoder = Encoder(lib, self.out_rate)
        frames = [encoder.encode(pcm[i:i + size], frame) for i in range(0, len(pcm), size)]
        log.info('wake acknowledgement %r ready (%.2fs)', text, len(pcm) / 2 / self.out_rate)
        return frames, text

    def status(self):
        tts = self.tts.health() if self.tts else None
        return {
            'ok': True, 'uptime_s': int(time.time() - self.started_at), 'sessions': len(self.sessions),
            'llm': {'url': self.brain.base_url if self.brain else None, 'up': bool(self.brain and self.brain.health())},
            'tts': {'url': self.tts.base_url if self.tts else None, 'up': bool(tts), 'busy': bool(tts and tts.get('busy'))},
            'wake_ack': bool(self.wake_ack[0]),
        }

    def ota_payload(self, request_host):
        cfg = self.cfg
        host = cfg['advertise_host'] or (request_host or '').split(':')[0] or lan_ip()
        port = cfg['advertise_port'] or self.ws_port or cfg['listen']['port']
        return {
            'server_time': {'timestamp': int(time.time() * 1000), 'timezone_offset': -time.timezone // 60},
            'firmware': {'version': '0.0.0', 'url': ''},
            'websocket': {'url': f"ws://{host}:{port}{cfg['ws_path']}"},
        }


def make_http_server(ctx, host, port):
    """OTA and status over plain HTTP. websockets' process_request cannot take a request body, and the
    firmware POSTs its device info to the OTA endpoint, so this runs on its own port (xiaozhi uses 8003 too)."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def _reply(self, status, body):
            data = (json.dumps(body, ensure_ascii=False) + '\n').encode('utf-8') if not isinstance(body, bytes) else body
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _route(self):
            path = self.path.split('?')[0]
            length = int(self.headers.get('Content-Length') or 0)
            if length:
                self.rfile.read(min(length, 1 << 16))
            if path.startswith('/xiaozhi/ota'):
                self._reply(HTTPStatus.OK, ctx.ota_payload(self.headers.get('Host')))
            elif path in ('/', '/health', '/status'):
                self._reply(HTTPStatus.OK, ctx.status())
            elif path == '/dashboard':
                dashboard.serve_page(self)
            elif path == '/events':
                dashboard.serve_events(self, ctx.bus)
            else:
                self._reply(HTTPStatus.NOT_FOUND, {'error': 'not found'})

        do_GET = do_POST = _route

    return ThreadingHTTPServer((host, port), Handler)


def make_process_request(ctx):
    """Plain GET on the websocket port still answers /health and /xiaozhi/ota/ (no body possible here)."""
    def process_request(connection, request):
        if 'upgrade' in request.headers.get('Connection', '').lower():
            return None
        path = request.path.split('?')[0]
        if path.startswith('/xiaozhi/ota'):
            body = ctx.ota_payload(request.headers.get('Host'))
        elif path in ('/', '/health', '/status'):
            body = ctx.status()
        else:
            return connection.respond(HTTPStatus.NOT_FOUND, 'not found\n')
        response = connection.respond(HTTPStatus.OK, json.dumps(body, ensure_ascii=False) + '\n')
        response.headers['Content-Type'] = 'application/json; charset=utf-8'
        return response
    return process_request


def make_handler(ctx):
    async def handler(ws):
        token = ctx.cfg.get('token')
        if token and ws.request.headers.get('Authorization', '') != f'Bearer {token}':
            log.warning('rejected %s: bad token', ws.remote_address)
            await ws.close(1008, 'unauthorized')
            return
        session = Session(ws, ctx)
        ctx.sessions.add(session)
        try:
            await session.run()
        finally:
            ctx.sessions.discard(session)
    return handler


async def run_server(ctx, ready=None):
    cfg = ctx.cfg
    listen = cfg['listen']
    httpd = make_http_server(ctx, listen['host'], listen['http_port'])
    ctx.http_port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True, name='http').start()
    try:
        async with serve(make_handler(ctx), listen['host'], listen['port'],
                         process_request=make_process_request(ctx), max_size=2 ** 20,
                         ping_interval=cfg['ws_ping_interval'], ping_timeout=cfg['ws_ping_timeout']) as server:
            ctx.ws_port = server.sockets[0].getsockname()[1]
            if ready is not None:
                ready.set_result(server)
            shown = cfg['advertise_host'] or lan_ip()
            log.info('websocket on %s:%s, http on :%s  ->  point the device OTA at http://%s:%s/xiaozhi/ota/',
                     listen['host'], listen['port'], ctx.http_port, shown, ctx.http_port)
            await server.serve_forever()
    finally:
        httpd.shutdown()
        httpd.server_close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config', default=None, help='YAML config (default: config.yaml next to this file, '
                                                       'falling back to config.example.yaml)')
    parser.add_argument('--host')
    parser.add_argument('--port', type=int)
    parser.add_argument('-v', '--verbose', action='store_true')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S')
    logging.getLogger('websockets').setLevel(logging.WARNING)
    for noisy in ('httpx', 'httpcore'):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    config_path = Path(args.config) if args.config else HERE / 'config.yaml'
    if not config_path.exists() and not args.config:
        config_path = HERE / 'config.example.yaml'
        log.warning('no config.yaml, using %s', config_path.name)
    cfg = load_config(config_path)
    if args.host:
        cfg['listen']['host'] = args.host
    if args.port:
        cfg['listen']['port'] = args.port
    ctx = Context.build(cfg)
    if not ctx.brain.health():
        log.warning('LLM at %s not reachable yet; replies will fail until it is up', ctx.brain.base_url)
    try:
        asyncio.run(run_server(ctx))
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()

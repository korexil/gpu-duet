"""Streaming HTTP TTS service (also works as xiaozhi-esp32-server's index_stream backend).

GET  /health      {"ready": true, "busy": bool, ...}   busy drives the LLM turn-taking
POST /stream      {"text": "..."} -> chunked raw PCM, s16le 24 kHz mono (audio/pcm)
POST /synthesize  {"text": "..."} -> audio/wav

Example:
  python server.py --library build/qwen3_tts_bridge.dll --dll-dir build --dll-dir "%CUDA_PATH%/bin" ^
      --models models-f16 --icl-prompt voice/icl-prompt.json --port 18766
"""
import argparse
import io
import json
import os
import select
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import soundfile as sf

# Storage switches of the patched engine (see native-tts/README.md): QKV dedup and a
# read-only, CPU-resident text embedding table save ~1.6 GB GPU + ~0.6 GB RAM with
# byte-identical PCM on the tested configurations.
ENGINE_ENV = dict(QWEN3_TTS_LAZY_BATCH_DECODER='1', QWEN3_TTS_LOW_MEM='0', QWEN3_TTS_QKV_DEDUP='1',
                  QWEN3_TTS_TEXT_EMBD_CPU='1', QWEN3_TTS_TEXT_EMBD_MMAP='1')


def make_server(tts, leveler, port, log_path=None, host='127.0.0.1'):
    busy = threading.Lock()

    def log(entry):
        if log_path:
            with Path(log_path).open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(entry) + '\n')

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path != '/health':
                self.send_error(404)
                return
            body = json.dumps(dict(ready=True, busy=busy.locked(), backend=tts.backend,
                                   active_backend=tts.active_backend,
                                   sentence_pause=leveler.sentence_pause)).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path not in ('/stream', '/synthesize'):
                self.send_error(404)
                return
            self.connection.settimeout(10)
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 8192:
                    raise ValueError('request body must be 1-8192 bytes')
                text = json.loads(self.rfile.read(size)).get('text')
                if not isinstance(text, str) or not 0 < len(text.strip()) <= 300:
                    raise ValueError('text must be 1-300 characters')
            except (ValueError, TypeError, AttributeError, TimeoutError):
                self.close_connection = True
                self.send_error(400)
                return
            if not busy.acquire(blocking=False):
                self.send_error(503, 'busy')
                return
            sent, collected = False, []

            def emit(wave):
                nonlocal sent
                if select.select([self.connection], [], [], 0)[0] and not self.connection.recv(1, socket.MSG_PEEK):
                    raise BrokenPipeError('client closed')
                if self.path == '/synthesize':
                    collected.append(wave.copy())
                    return
                if not sent:
                    self.send_response(200)
                    self.send_header('Content-Type', 'audio/pcm;rate=24000;channels=1;format=s16le')
                    self.send_header('Transfer-Encoding', 'chunked')
                    self.send_header('Cache-Control', 'no-store')
                    self.end_headers()
                    sent = True
                data = (np.clip(wave, -1, 1) * 32767).astype('<i2').tobytes()
                self.wfile.write(f'{len(data):X}\r\n'.encode() + data + b'\r\n')
                self.wfile.flush()

            try:
                stage = leveler.begin(text)

                def leveled(wave):
                    out = stage.process(wave)
                    if len(out):
                        emit(out)

                metrics = tts.synthesize(text, leveled)
                if metrics['success']:
                    rest = stage.finish()
                    if len(rest):
                        emit(rest)
                if metrics['cancelled']:
                    self.close_connection = True
                elif self.path == '/stream':
                    if not sent:
                        raise RuntimeError('empty stream')
                    self.wfile.write(b'0\r\n\r\n')
                    self.wfile.flush()
                else:
                    output = io.BytesIO()
                    sf.write(output, np.concatenate(collected), 24000, format='WAV', subtype='PCM_16')
                    data = output.getvalue()
                    self.send_response(200)
                    self.send_header('Content-Type', 'audio/wav')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                log(dict(path=self.path, chars=len(text), output_samples=metrics['output_samples'],
                         wall_seconds=metrics.get('wall_seconds'), cancelled=metrics['cancelled']))
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True
            except Exception as error:
                log(dict(error=str(error)))
                if not sent:
                    self.send_error(500, 'synthesis failed')
                self.close_connection = True
            finally:
                busy.release()

    return ThreadingHTTPServer((host, port), Handler)


def default_dll_dirs(library):
    """The library's folder (and its bin/ with the ggml DLLs), plus the CUDA runtime and cuBLAS
    from the nvidia-cuda-runtime-cu12 / nvidia-cublas-cu12 wheels when they are installed."""
    here = Path(library).resolve().parent
    dirs = [here, here / 'bin']
    try:
        import nvidia
        dirs += [Path(base) / name / 'bin' for base in nvidia.__path__ for name in ('cuda_runtime', 'cublas')]
    except ImportError:
        pass
    return [str(d) for d in dirs if d.is_dir()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--library', required=True, help='qwen3_tts_bridge shared library')
    parser.add_argument('--dll-dir', action='append', default=[], help='extra DLL search dirs, e.g. a CUDA toolkit bin')
    parser.add_argument('--models', required=True, help='directory with the converted F16 GGUF models')
    parser.add_argument('--icl-prompt', required=True, help='voice clone prompt from --extract-icl-prompt')
    parser.add_argument('--backend', choices=['cuda', 'cpu'], default='cuda')
    parser.add_argument('--language', default='zh', help='zh en ja ko de fr es it pt ru')
    parser.add_argument('--port', type=int, default=18766)
    parser.add_argument('--sentence-pause', type=float, default=0.9, help='seconds of silence after each sentence')
    parser.add_argument('--log')
    args = parser.parse_args()
    for key, value in ENGINE_ENV.items():
        os.environ.setdefault(key, value)

    from leveler import Leveler
    from native_engine import NativeTTS

    tts = NativeTTS(args.library, args.models, args.icl_prompt, backend=args.backend,
                    dll_dirs=default_dll_dirs(args.library) + args.dll_dir, language=args.language)
    try:
        warm = tts.synthesize({'zh': '嗯。', 'ja': 'うん。'}.get(args.language, 'OK.'), lambda wave: None)
        if not warm['success']:
            raise RuntimeError('warmup failed')
        server = make_server(tts, Leveler(sentence_pause=args.sentence_pause), args.port, args.log)
        print(json.dumps(dict(ready=True, port=args.port, active_backend=tts.active_backend)), flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
    finally:
        tts.close()


if __name__ == '__main__':
    main()

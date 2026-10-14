"""Streaming HTTP TTS service (also works as xiaozhi-esp32-server's index_stream backend).

GET  /health      {"ready": true, "busy": bool, "started": n, "served": n, ...}
                  busy and the counters drive the LLM turn-taking
POST /stream      {"text": "..."} -> chunked raw PCM, s16le 24 kHz mono (audio/pcm)
POST /synthesize  {"text": "..."} -> audio/wav

One sentence is synthesized at a time. A second request queues for up to
--queue-timeout seconds and then gets 503; it is never run concurrently on the GPU.

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
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import soundfile as sf

# Storage switches of the patched engine (see native-tts/README.md): QKV dedup and a
# read-only, CPU-resident text embedding table save ~1.1 GB GPU (488 + 594 MiB) + ~0.6 GB RAM with
# byte-identical PCM on the tested configurations.
ENGINE_ENV = dict(QWEN3_TTS_LAZY_BATCH_DECODER='1', QWEN3_TTS_LOW_MEM='0', QWEN3_TTS_QKV_DEDUP='1',
                  QWEN3_TTS_TEXT_EMBD_CPU='1', QWEN3_TTS_TEXT_EMBD_MMAP='1',
                  # the talker stops while the last syllable is still sounding; the engine repeats the
                  # last frame this many times so the vocoder renders the release (0.24 s)
                  QWEN3_TTS_TAIL_PAD_FRAMES='3')


def make_server(tts, leveler, port, log_path=None, host='127.0.0.1', queue_timeout=30.0):
    busy = threading.Lock()
    counters = dict(started=0, served=0)   # monotonic; the LLM provider watches them

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
                                   active_backend=tts.active_backend, sentence_pause=leveler.sentence_pause,
                                   **counters)).encode()
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
            def client_closed():
                return bool(select.select([self.connection], [], [], 0)[0]) and not self.connection.recv(1, socket.MSG_PEEK)

            # Queue behind the running sentence, but never start one nobody is waiting for:
            # a client that gave up (the user interrupted) is noticed while queuing, not after
            # its first chunk was already synthesized.
            deadline = time.monotonic() + queue_timeout
            while not busy.acquire(timeout=0.05):
                if client_closed():
                    self.close_connection = True
                    return
                if time.monotonic() > deadline:
                    self.send_error(503, 'busy')
                    return
            if client_closed():
                busy.release()
                self.close_connection = True
                return
            counters['started'] += 1
            sent, collected = False, []

            def emit(wave):
                nonlocal sent
                if client_closed():
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
                if metrics['truncated']:
                    print(f'warning: hit the {metrics["token_budget"]}-token budget on {len(text)} chars, '
                          f'the end of the sentence is missing: {text!r}', file=sys.stderr, flush=True)
                log(dict(path=self.path, chars=len(text), output_samples=metrics['output_samples'],
                         wall_seconds=metrics.get('wall_seconds'), cancelled=metrics['cancelled'],
                         truncated=metrics['truncated'], seed=metrics['seed']))
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True
            except Exception as error:
                log(dict(error=str(error)))
                if not sent:
                    self.send_error(500, 'synthesis failed')
                self.close_connection = True
            finally:
                counters['served'] += 1
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


def parse_seed(value):
    if value == 'random':
        return None
    return int(value)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--library', required=True, help='qwen3_tts_bridge shared library')
    parser.add_argument('--dll-dir', action='append', default=[], help='extra DLL search dirs, e.g. a CUDA toolkit bin')
    parser.add_argument('--models', required=True, help='directory with the converted F16 GGUF models')
    parser.add_argument('--icl-prompt', help='voice clone prompt from --extract-icl-prompt (Base talker)')
    parser.add_argument('--speaker', help='named speaker of a CustomVoice talker, e.g. vivian, ryan, aiden')
    parser.add_argument('--instruct', help='CustomVoice style instruction, e.g. "语速放慢，轻快有起伏，语气自然"')
    parser.add_argument('--backend', choices=['cuda', 'cpu'], default='cuda')
    parser.add_argument('--language', default='zh', help='zh en ja ko de fr es it pt ru')
    parser.add_argument('--port', type=int, default=18766)
    parser.add_argument('--sentence-pause', type=float, default=0.9, help='seconds of silence after each sentence')
    parser.add_argument('--clause-chars', type=int, default=6,
                        help='sentences up to this many characters get the shorter clause pause')
    parser.add_argument('--seed', type=parse_seed, default=7,
                        help='sampling seed; the same sentence always sounds the same. "random" draws one per sentence')
    parser.add_argument('--queue-timeout', type=float, default=30.0,
                        help='seconds a second request waits for the engine before 503')
    parser.add_argument('--log')
    args = parser.parse_args()
    for key, value in ENGINE_ENV.items():
        os.environ.setdefault(key, value)

    from leveler import Leveler
    from native_engine import NativeTTS

    if bool(args.icl_prompt) == bool(args.speaker):
        parser.error('give exactly one of --icl-prompt (voice clone) or --speaker (CustomVoice model)')
    tts = NativeTTS(args.library, args.models, args.icl_prompt, backend=args.backend,
                    dll_dirs=default_dll_dirs(args.library) + args.dll_dir, language=args.language, seed=args.seed,
                    speaker=args.speaker, instruct=args.instruct)
    try:
        warm = tts.synthesize({'zh': '嗯。', 'ja': 'うん。'}.get(args.language, 'OK.'), lambda wave: None)
        if not warm['success']:
            raise RuntimeError('warmup failed')
        leveler = Leveler(sentence_pause=args.sentence_pause, clause_chars=args.clause_chars)
        server = make_server(tts, leveler, args.port, args.log, queue_timeout=args.queue_timeout)
        print(json.dumps(dict(ready=True, port=args.port, active_backend=tts.active_backend,
                              abi_check=tts.abi_check)), flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
    finally:
        tts.close()


if __name__ == '__main__':
    main()

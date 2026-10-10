"""Compare LLM/TTS GPU turn-taking on/off without xiaozhi in the loop.

Runs the llamacpp_paced provider against a llama-server, cuts its stream into
sentences like the xiaozhi TTS segmenter, synthesizes them one by one with the
TTS service and simulates a player that starts after 0.5 s of buffered audio.

  python turn_taking_sim.py --mode plain --question "Tell me about your day, a bit longer."
  python turn_taking_sim.py --mode turns --question "Tell me about your day, a bit longer."

Wait a few seconds between runs so the GPU clocks down; the first sentence after
idle is the hardest case.
"""
import argparse
import importlib.util
import json
import queue
import sys
import threading
import time
import types
import urllib.request
from pathlib import Path

PROVIDER = Path(__file__).resolve().parents[1] / 'server/core/providers/llm/llamacpp_paced/llamacpp_paced.py'


def load_provider():
    for name in ('config', 'config.logger', 'core', 'core.providers', 'core.providers.llm', 'core.providers.llm.base'):
        sys.modules.setdefault(name, types.ModuleType(name))

    class _Log:
        def bind(self, **_):
            return self

        def __getattr__(self, _):
            return lambda *a, **k: None
    sys.modules['config.logger'].setup_logging = lambda: _Log()
    sys.modules['core.providers.llm.base'].LLMProviderBase = object
    spec = importlib.util.spec_from_file_location('llamacpp_paced', PROVIDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.LLMProvider


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['plain', 'turns'], required=True)
    parser.add_argument('--question', required=True)
    parser.add_argument('--system', default='')
    parser.add_argument('--llm', default='http://127.0.0.1:18080')
    parser.add_argument('--tts', default='http://127.0.0.1:18766')
    parser.add_argument('--start-buffer', type=float, default=0.5)
    args = parser.parse_args()

    config = {'base_url': args.llm, 'max_tokens': 400}
    if args.mode == 'turns':
        config['voice_health_url'] = args.tts + '/health'
    provider = load_provider()(config)
    dialogue = ([{'role': 'system', 'content': args.system}] if args.system else []) + \
        [{'role': 'user', 'content': args.question}]

    sentences, arrivals = queue.Queue(), []
    t0 = time.time()

    def tts_worker():
        while (text := sentences.get()) is not None:
            request = urllib.request.Request(args.tts + '/stream', data=json.dumps({'text': text}).encode(),
                                             headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=120) as response:
                while chunk := response.read(2880):
                    arrivals.append((time.time() - t0, len(chunk) / 48000))

    worker = threading.Thread(target=tts_worker)
    worker.start()
    buffer, count = '', 0
    for piece in provider.response('sim', dialogue):
        buffer += piece
        while (cut := min([buffer.index(c) for c in '。！？!?\n' if c in buffer] or [-1])) >= 0:
            sentence, buffer = buffer[:cut + 1].strip(), buffer[cut + 1:]
            if sentence:
                sentences.put(sentence)
                count += 1
    llm_done = time.time() - t0
    if buffer.strip():
        sentences.put(buffer.strip())
        count += 1
    sentences.put(None)
    worker.join()

    buffered, start, clock, underrun, worst = 0.0, None, None, 0.0, 0.0
    for t, seconds in arrivals:
        if start is None:
            buffered += seconds
            if buffered >= args.start_buffer:
                start, clock = t, t + buffered
            continue
        if t > clock:
            underrun += t - clock
            worst = max(worst, t - clock)
            clock = t
        clock += seconds
    print(json.dumps(dict(mode=args.mode, first_audio_s=round(start, 2) if start else None,
                          llm_done_s=round(llm_done, 2), audio_s=round(sum(s for _, s in arrivals), 1),
                          sentences=count, underrun_total_s=round(underrun, 2), worst_gap_s=round(worst, 2))))


if __name__ == '__main__':
    main()

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


def run(mode, question, system='', llm='http://127.0.0.1:18080', tts='http://127.0.0.1:18766',
        start_buffer=0.5, seed=None):
    """One answer through LLM -> sentence splitter -> TTS -> simulated player; returns the metrics."""
    config = {'base_url': llm, 'max_tokens': 400}
    if mode == 'turns':
        config['voice_health_url'] = tts + '/health'
    provider = load_provider()(config)
    if seed is not None:
        provider.sampling['seed'] = seed  # same seed -> same text in both modes
    dialogue = ([{'role': 'system', 'content': system}] if system else []) + \
        [{'role': 'user', 'content': question}]

    sentences, arrivals, spoken = queue.Queue(), [], []
    t0 = time.time()

    def tts_worker():
        while (text := sentences.get()) is not None:
            request = urllib.request.Request(tts + '/stream', data=json.dumps({'text': text}).encode(),
                                             headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=120) as response:
                while chunk := response.read(2880):
                    arrivals.append((time.time() - t0, len(chunk) / 48000))

    def say(sentence):
        spoken.append(sentence)
        sentences.put(sentence)

    worker = threading.Thread(target=tts_worker)
    worker.start()
    buffer = ''
    for piece in provider.response('sim', dialogue):
        buffer += piece
        while (cut := min([buffer.index(c) for c in '。！？!?\n' if c in buffer] or [-1])) >= 0:
            sentence, buffer = buffer[:cut + 1].strip(), buffer[cut + 1:]
            if sentence:
                say(sentence)
    llm_done = time.time() - t0
    if buffer.strip():
        say(buffer.strip())
    sentences.put(None)
    worker.join()

    buffered, start, clock, underrun, worst = 0.0, None, None, 0.0, 0.0
    for t, seconds in arrivals:
        if start is None:
            buffered += seconds
            if buffered >= start_buffer:
                start, clock = t, t + buffered
            continue
        if t > clock:
            underrun += t - clock
            worst = max(worst, t - clock)
            clock = t
        clock += seconds
    lengths = sorted(len(x) for x in spoken)
    return dict(mode=mode, first_audio_s=round(start, 2) if start else None, llm_done_s=round(llm_done, 2),
                audio_s=round(sum(s for _, s in arrivals), 1), sentences=len(spoken),
                sentence_chars_median=lengths[len(lengths) // 2] if lengths else 0,
                sentence_chars_max=lengths[-1] if lengths else 0,
                underrun_total_s=round(underrun, 2), worst_gap_s=round(worst, 2), text=''.join(spoken))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['plain', 'turns'], required=True)
    parser.add_argument('--question', required=True)
    parser.add_argument('--system', default='')
    parser.add_argument('--llm', default='http://127.0.0.1:18080')
    parser.add_argument('--tts', default='http://127.0.0.1:18766')
    parser.add_argument('--start-buffer', type=float, default=0.5)
    parser.add_argument('--seed', type=int)
    args = parser.parse_args()
    result = run(args.mode, args.question, args.system, args.llm, args.tts, args.start_buffer, args.seed)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()

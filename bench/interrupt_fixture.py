"""Interrupt fixture: ask, press the button mid-answer, ask again at once, optionally with a TTS fault.

  python bench/interrupt_fixture.py --ws 8020 --http 8023 --out results/interrupt/clean.json
  python bench/interrupt_fixture.py ... --proxy http://127.0.0.1:18767 --fault error --out .../error.json
  python bench/interrupt_fixture.py ... --proxy http://127.0.0.1:18767 --fault hang  --out .../hang.json

Subscribes to the server's /events before asking, so both answers' events (each tagged with its
generation, ``gen``) are captured. Then, through standalone/loopback.py: question A; an abort (what the
board's button sends) --abort-after seconds into A's audio; question B right away. With --fault, the
fault proxy (bench/tts_fault_proxy.py, which the server's tts.base_url must point at) is armed just
before B, so B's first sentence gets a 500 or a hang.

Reported: whether any event of A's generation appears after B's question; B's first frame; how long
B's first TTS request waited for audio (tts_start -> first pcm; 1.0-1.2 s when the TTS is free); and
with a fault, when B's answer ended. Everything is on the server's timeline (see bench/results/
traces-2026-10-16/README.md for what "first frame" does and doesn't include).
"""
import argparse
import asyncio
import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'standalone'))
import loopback  # noqa: E402

QUESTION_A = '讲一个关于蓝色鹦鹉和月亮的故事，三百字左右。'
QUESTION_B = '现在换个话题，用一句话说说大海是什么颜色。'


class EventTap:
    """Collect /events from now on (the replay of an earlier answer is dropped) until stopped."""

    def __init__(self, http_port):
        self.url = f'http://127.0.0.1:{http_port}/events'
        self.events, self.connected, self._stop = [], None, threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()
        deadline = time.monotonic() + 5
        while self.connected is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if self.connected is None:
            raise RuntimeError(f'could not connect to {self.url}')
        return self

    def _run(self):
        with urllib.request.urlopen(self.url, timeout=10) as response:
            self.connected = time.time()
            while not self._stop.is_set():
                line = response.readline()
                if not line:
                    break
                if line.startswith(b'data: '):
                    event = json.loads(line[6:])
                    if event['t'] >= self.connected - 0.5 and event['type'] != 'metrics':
                        self.events.append(event)

    def stop(self):
        self._stop.set()


def arm_fault(proxy, mode):
    body = json.dumps({'mode': mode, 'count': 1}).encode()
    request = urllib.request.Request(proxy.rstrip('/') + '/fault', data=body,
                                     headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(request, timeout=5) as r:
        return json.loads(r.read())


def analyse(events):
    questions = [e for e in events if e['type'] == 'question']
    if len(questions) < 2:
        return dict(error=f'{len(questions)} question(s) in the capture, expected 2')
    gen_a, gen_b = questions[0]['gen'], questions[1]['gen']
    tb = questions[1]['t']
    after_b = [e for e in events if e['t'] >= tb]
    late = [e for e in after_b if e.get('gen') == gen_a]
    leaked = [e for e in late if e['type'] in ('tts_start', 'sentence', 'pcm', 'release', 'frame')]
    b = [e for e in after_b if e.get('gen') == gen_b]
    first = lambda kind: next((e['t'] - tb for e in b if e['type'] == kind), None)
    a_frames = [e['t'] for e in events if e.get('gen') == gen_a and e['type'] == 'frame']
    a_stop = next((e['t'] for e in events if e.get('gen') == gen_a and e['type'] == 'stop'), None)
    tts_start, first_pcm = first('tts_start'), first('pcm')
    return dict(
        gen_a=gen_a, gen_b=gen_b,
        leaked_after_b={t: sum(1 for x in leaked if x['type'] == t) for t in sorted({x['type'] for x in leaked})},
        a_events_after_b={t: sum(1 for x in late if x['type'] == t) for t in sorted({x['type'] for x in late})},
        a_last_frame_before_b_s=round(tb - a_frames[-1], 3) if a_frames else None,
        a_stop_before_b_s=round(tb - a_stop, 3) if a_stop else None,
        b_tts_start_s=None if tts_start is None else round(tts_start, 3),
        b_first_pcm_s=None if first_pcm is None else round(first_pcm, 3),
        b_tts_wait_s=None if None in (tts_start, first_pcm) else round(first_pcm - tts_start, 3),
        b_first_frame_s=None if first('frame') is None else round(first('frame'), 3),
        b_stop_s=None if first('stop') is None else round(first('stop'), 3),
        b_frames=sum(1 for e in b if e['type'] == 'frame'),
        b_gaps=[round(e['gap'], 3) for e in b if e['type'] == 'frame' and e['gap'] >= 0.01],
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--ws', type=int, default=8000)
    ap.add_argument('--http', type=int, default=8012)
    ap.add_argument('--abort-after', type=float, default=2.0, help='seconds of A audio before the abort')
    ap.add_argument('--fault', choices=['none', 'error', 'hang'], default='none')
    ap.add_argument('--proxy', default='http://127.0.0.1:18767', help='fault proxy, needed with --fault')
    ap.add_argument('--out', help='write events + analysis as JSON here')
    a = ap.parse_args()

    tap = EventTap(a.http).start()
    before_then = (lambda: arm_fault(a.proxy, a.fault)) if a.fault != 'none' else None
    answers = asyncio.run(loopback.run(f'ws://127.0.0.1:{a.ws}/xiaozhi/v1/', None, QUESTION_A, None,
                                       abort_after=a.abort_after, then=QUESTION_B, before_then=before_then))
    time.sleep(1.0)   # let the last events land
    tap.stop()
    summary = analyse(tap.events)
    summary.update(fault=a.fault, abort_after_s=a.abort_after,
                   device=dict(a_first_audio_s=answers[0]['first_audio'], a_aborted_at_s=answers[0]['aborted_at'],
                               a_frames_after_abort=answers[0]['frames_after_abort'],
                               b_first_audio_s=answers[1]['first_audio'], b_stop_s=answers[1]['stop']))
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(dict(summary=summary, events=tap.events), ensure_ascii=False), encoding='utf-8')
        print('saved', a.out)


if __name__ == '__main__':
    main()

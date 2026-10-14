"""Measure what the standalone server's downlink AGC does to loudness, on a reply synthesized now.

  python tools/agc_check.py                       # synthesize the three fixed replies via tts-service, report
  python tools/agc_check.py --wav reply.wav       # or measure an existing 24 kHz mono WAV
  python tools/agc_check.py --save work/agc       # also write <dir>/<reply>-noagc.wav and -agc.wav

The reply is cut into chunks with the standalone splitter (same defaults as config.example.yaml),
each chunk synthesized by tts-service's POST /stream, trailing silence trimmed to 400 ms as the
server does, then joined. The AGC is standalone/agc.py with its defaults, run 60 ms frame by frame.

Metric: loudness of every 0.25 s window above -40 dBFS (silence excluded); reported as the spread
between the 10th and 90th percentile and the standard deviation, in dB, before and after the AGC.
Max - min is not used: it hangs on one quiet window at a word boundary and moves by 5 dB run to run.
Three fixed replies (narrative, chatty, explanatory); tts-service started with a fixed --seed gives
the same audio every run.
"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'standalone'))
from agc import Agc  # noqa: E402
from brain import SentenceSplitter  # noqa: E402

RATE = 24000
FRAME = RATE * 60 // 1000
WINDOW_S, FLOOR_DB, TAIL_KEEP_S = 0.25, -40.0, 0.4
REPLIES = {
    'narrative': ('今天下午我去公园走了一圈，风有点大，树叶一直在响。路边有个卖烤红薯的小摊，我买了一个，'
                  '捧在手里很暖和。走到湖边的时候，看到几只鸭子在水里慢慢游，一点也不着急。'
                  '天快黑的时候路灯亮了，我就沿着原路回家了。回来以后泡了一杯茶，坐在窗边发了一会儿呆，'
                  '觉得这样安安静静的一天也挺好的。'),
    'chatty': ('哈哈，你这么一说我也想起来了！上次我们去吃的那家火锅，汤底真的特别香，就是排队排了快一个小时。'
               '不过说实话，等的时候聊天也挺开心的，一点都不觉得久。下次要不要早点去？我觉得五点左右人应该少一些。'
               '对了，你上回说想试试他们家的番茄锅，这次可以点一个鸳鸯锅，两种都尝尝。'),
    'explain': ('显卡的显存可以理解成它的工作台。模型要先整个搬到工作台上才能开始干活，工作台不够大，'
                '就只能把一部分放回系统内存，每次用到再搬回来，速度会慢很多。所以在八 G 的显卡上同时放两个模型，'
                '关键是让它们加起来不超过工作台的大小，并且别同时抢着用。'),
}


def synthesize(tts, text):
    request = urllib.request.Request(tts + '/stream', data=json.dumps({'text': text}, ensure_ascii=False).encode('utf-8'),
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=180) as response:
        return np.frombuffer(response.read(), dtype=np.int16)


def trim_tail(pcm, floor=300):
    loud = np.flatnonzero(np.abs(pcm) > floor)
    keep = int(RATE * TAIL_KEEP_S)
    return pcm[:keep] if not len(loud) else pcm[:min(len(pcm), int(loud[-1]) + 1 + keep)]


def apply_agc(pcm):
    agc = Agc(RATE)
    pad = (-len(pcm)) % FRAME
    x = np.concatenate([pcm, np.zeros(pad, dtype=np.int16)])
    return np.concatenate([agc.process(x[i:i + FRAME]) for i in range(0, len(x), FRAME)])[:len(pcm)]


def spread(pcm):
    x = pcm.astype(np.float64) / 32768.0
    n = int(RATE * WINDOW_S)
    windows = x[:len(x) // n * n].reshape(-1, n)
    db = 20 * np.log10(np.sqrt(np.mean(windows ** 2, axis=1)) + 1e-12)
    db = db[db > FLOOR_DB]
    return float(np.percentile(db, 90) - np.percentile(db, 10)), float(db.std()), len(db)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tts', default='http://127.0.0.1:18766')
    ap.add_argument('--wav')
    ap.add_argument('--save')
    a = ap.parse_args()
    sources = {}
    if a.wav:
        data, rate = sf.read(a.wav, dtype='int16')
        assert rate == RATE, f'need {RATE} Hz, got {rate}'
        sources[Path(a.wav).stem] = data if data.ndim == 1 else data[:, 0]
    else:
        for name, text in REPLIES.items():
            splitter = SentenceSplitter(min_chars=30, max_chars=70, first_min_chars=12)
            chunks = splitter.feed(text) + ([r] if (r := splitter.flush()) else [])
            sources[name] = np.concatenate([trim_tail(synthesize(a.tts, c)) for c in chunks])
    for name, raw in sources.items():
        out = apply_agc(raw)
        (r0, s0, n0), (r1, s1, n1) = spread(raw), spread(out)
        print(f'{name:10s} {len(raw) / RATE:5.1f} s   p90-p10 {r0:4.1f} -> {r1:4.1f} dB   std {s0:3.1f} -> {s1:3.1f} dB   ({n0}/{n1} windows)')
        if a.save:
            Path(a.save).mkdir(parents=True, exist_ok=True)
            sf.write(Path(a.save) / f'{name}-noagc.wav', raw, RATE, subtype='PCM_16')
            sf.write(Path(a.save) / f'{name}-agc.wav', out, RATE, subtype='PCM_16')


if __name__ == '__main__':
    main()

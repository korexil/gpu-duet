"""Loudness / pause / peak report for replies captured by device_probe.py.

  python loudness_stats.py probe-out/*.wav

sentence_spread: max-min loudness of sentences (split at >=300 ms of silence)
pauses:          silences >=300 ms between sentences
pops:            isolated bursts <0.3 s that are >8 dB louder than the median phrase
"""
import sys

import numpy as np
import soundfile as sf

FLOOR_DB = -45


def segments(voiced, min_gap):
    out, start, gap = [], None, 0
    for index, flag in enumerate(voiced):
        if flag:
            start = index if start is None else start
            gap = 0
        elif start is not None:
            gap += 1
            if gap >= min_gap:
                out.append((start, index - gap))
                start, gap = None, 0
    if start is not None:
        out.append((start, len(voiced) - 1))
    return out


def report(path):
    wave, rate = sf.read(path, dtype="float64")
    wave = wave if wave.ndim == 1 else wave.mean(axis=1)
    frame = int(rate * 0.05)
    rms = np.sqrt(np.mean(wave[:len(wave) // frame * frame].reshape(-1, frame) ** 2, axis=1))
    db = 20 * np.log10(rms + 1e-12)
    voiced = db > FLOOR_DB
    sentences = [(a, b) for a, b in segments(voiced, 6) if (b - a + 1) * 0.05 >= 0.4]
    loud = [20 * np.log10(np.sqrt(np.mean(rms[a:b + 1][voiced[a:b + 1]] ** 2))) for a, b in sentences]
    phrases = segments(voiced, 3)
    phrase_db = [float(np.mean(db[a:b + 1][voiced[a:b + 1]])) for a, b in phrases]
    lengths = [(b - a + 1) * 0.05 for a, b in phrases]
    long_ones = [d for d, n in zip(phrase_db, lengths) if n >= 0.3]
    median = float(np.median(long_ones)) if long_ones else 0.0
    pops = [round(a * 0.05, 2) for (a, b), d, n in zip(phrases, phrase_db, lengths) if n < 0.3 and d > median + 8]
    pauses = [(sentences[i + 1][0] - sentences[i][1]) * 50 for i in range(len(sentences) - 1)]
    return dict(file=path, seconds=round(len(wave) / rate, 2),
                loudness_db=round(float(20 * np.log10(np.sqrt(np.mean(rms[voiced] ** 2)) + 1e-12)), 1) if voiced.any() else None,
                peak_db=round(float(20 * np.log10(np.abs(wave).max() + 1e-12)), 1),
                above_minus3db_pct=round(float(np.mean(np.abs(wave) > 10 ** (-3 / 20)) * 100), 3),
                sentence_spread_db=round(float(max(loud) - min(loud)), 1) if loud else None,
                pauses_ms=[int(p) for p in pauses], pops_at_s=pops)


if __name__ == "__main__":
    for path in sys.argv[1:]:
        print(report(path))

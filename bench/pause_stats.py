"""Silence statistics of reply recordings, to compare two servers on the same answer.

  python bench/pause_stats.py a.wav b.wav [--floor 300] [--min-pause 0.25]

Per file: length, lead-in silence, every internal pause longer than --min-pause, trailing silence
and peak. "Silence" is |sample| below --floor (of 32767). Recordings come from
standalone/loopback.py --out.
"""
import argparse

import numpy as np
import soundfile as sf


def stats(path, floor, min_pause):
    x, rate = sf.read(path, dtype='int16')
    x = np.abs(x.astype(np.int32)) if x.ndim == 1 else np.abs(x.astype(np.int32)).max(axis=1)
    quiet = x < floor
    loud = np.flatnonzero(~quiet)
    if not len(loud):
        return dict(seconds=len(x) / rate, silent=True)
    pauses, i = [], loud[0]
    while i < loud[-1]:
        if quiet[i]:
            j = i
            while j < len(x) and quiet[j]:
                j += 1
            if (j - i) / rate >= min_pause:
                pauses.append(round(float(j - i) / rate, 2))
            i = j
        else:
            i += 1
    return dict(seconds=round(len(x) / rate, 2), lead_in=round(float(loud[0]) / rate, 2), pauses=pauses,
                trailing=round(float(len(x) - 1 - loud[-1]) / rate, 2), peak=int(x.max()))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('files', nargs='+')
    ap.add_argument('--floor', type=int, default=300)
    ap.add_argument('--min-pause', type=float, default=0.25)
    a = ap.parse_args()
    for f in a.files:
        s = stats(f, a.floor, a.min_pause)
        print(f'{f}: {s["seconds"]} s, lead-in {s.get("lead_in")} s, pauses >= {a.min_pause} s: {s.get("pauses")}, '
              f'trailing {s.get("trailing")} s, peak {s.get("peak")}')


if __name__ == '__main__':
    main()

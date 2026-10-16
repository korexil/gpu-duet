"""Analyse the PCM traces of the standalone server and replay its playback estimate.

  python bench/replay_trace.py bench/results/traces-2026-10-16

Each JSON file is the /events replay of one answer, recorded with the trace events in
standalone/session.py: tts_start (a sentence goes to the TTS), pcm (a PCM block arrives, with its
duration), chunk_end, release (frames handed to the Pacer after the tail hold) and frame (sent, with
the Pacer's gap estimate). Per run this prints:
- first audio broken down: TTS start -> first PCM -> first release -> first frame, and the size of
  the first PCM burst and how long the next block takes;
- per-sentence wall-clock RTF from the TTS request to its last PCM (includes the request latency);
- the Pacer replayed on the release times with a prebuffer of 5 / 7 / 9 frames. The 5-frame replay
  checks the replay against the gaps the server reported. The three are identical by construction:
  the prebuffer only limits how far ahead the server sends, and the gap estimate assumes playback
  starts with the first frame;
- a player that waits 0 / 0.12 / 0.3 / 0.5 s before starting, and the wait needed for zero gaps;
- the first segment again as if frames left as soon as PCM arrived (no tail hold). Only the first
  segment: after that, trimming the trailing silence changes the audio, which this can't model.
"""
import json
import statistics as st
import sys
from pathlib import Path

FRAME_S = 0.06


def pacer_replay(avail, prebuffer):
    """Same logic as standalone/session.py Pacer.slot, fed by frame availability times."""
    t0, sent, now, gaps, sends = None, 0, 0.0, [], []
    for a in avail:
        now = max(now, a)                          # the next frame can't go before it exists
        if t0 is None:
            t0 = now
        due = t0 + (sent - prebuffer) * FRAME_S
        gap = 0.0
        if now - due > prebuffer * FRAME_S:
            gap = now - due - prebuffer * FRAME_S
            t0 = now - sent * FRAME_S
            due = now - prebuffer * FRAME_S
        if due > now:
            now = due                              # asyncio.sleep(due - now)
        sends.append(now)
        sent += 1
        if gap > 1e-3:                             # ignore float noise
            gaps.append((now, gap))
    return sends, gaps


def device_replay(avail, start_delay):
    """A player that waits start_delay after the first frame, then plays one frame per 60 ms; on an
    underrun it resumes as soon as the next frame arrives (like the Pacer's re-anchor)."""
    if not avail:
        return []
    play_at, gaps = avail[0] + start_delay, []
    for a in avail:
        if a > play_at + 1e-3:
            gaps.append((play_at, a - play_at))
            play_at = a
        play_at += FRAME_S
    return gaps


def needed_delay(avail):
    """Smallest start delay with no underrun at all: the latest any frame is against the real-time schedule."""
    return max(0.0, max(a - (avail[0] + k * FRAME_S) for k, a in enumerate(avail)))


def startup_no_hold(pcm, first_end):
    """Frames available as PCM arrives (no tail hold), up to the end of the first segment only."""
    avail, acc, n = [], 0.0, 0
    for t, s in pcm:
        if first_end is not None and t > first_end:
            break
        acc += s
        while (n + 1) * FRAME_S <= acc + 1e-9:
            avail.append(t)
            n += 1
    return avail


def analyse(path):
    ev = json.loads(Path(path).read_text(encoding='utf-8'))
    q = [e for e in ev if e['type'] == 'question'][-1]['t']
    rel = lambda e: e['t'] - q
    pcm = [(rel(e), e['ms'] / 1000) for e in ev if e['type'] == 'pcm']
    starts = [rel(e) for e in ev if e['type'] == 'tts_start']
    ends = [rel(e) for e in ev if e['type'] == 'chunk_end']
    releases = [(rel(e), e['frames']) for e in ev if e['type'] == 'release']
    frames = [(rel(e), e['gap']) for e in ev if e['type'] == 'frame']
    llm_done = [rel(e) for e in ev if e['type'] == 'llm_done']

    avail = [t for t, n in releases for _ in range(n)]
    out = dict(run=Path(path).stem, tts_start=starts[0], first_pcm=pcm[0][0], first_release=releases[0][0],
               first_frame=frames[0][0], llm_done=llm_done[0] if llm_done else None,
               real_gaps=[g for _, g in frames if g > 0], real_gap_at=[round(t, 2) for t, g in frames if g >= 0.1])
    burst = [(tt, s) for tt, s in pcm if tt - pcm[0][0] < 0.01]
    out['burst_s'] = sum(s for _, s in burst)
    later = [tt for tt, _ in pcm if tt - pcm[0][0] >= 0.01]
    out['next_after_burst'] = (later[0] - pcm[0][0]) if later else None
    # first audio if frames could go out as soon as PCM arrives (no tail hold): when the first 60 ms exist
    acc = 0.0
    for t, s in pcm:
        acc += s
        if acc >= FRAME_S:
            out['first_frame_no_hold'] = t
            break
    # per-sentence RTF from PCM arrivals: wall time from TTS start to its last PCM / audio produced
    rtf = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        chunk = [(t, d) for t, d in pcm if t >= s and (e is None or t <= e)]
        if chunk and sum(d for _, d in chunk) > 0.5:
            rtf.append((chunk[-1][0] - s) / sum(d for _, d in chunk))
    out['rtf_first'] = rtf[0] if rtf else None
    out['rtf_median'] = st.median(rtf) if rtf else None
    for n in (5, 7, 9):
        sends, gaps = pacer_replay(avail, n)
        out[f'pb{n}'] = (len(gaps), round(sum(g for _, g in gaps), 2), round(max((g for _, g in gaps), default=0), 2),
                         [round(t, 2) for t, g in gaps if g >= 0.1])
    out['delays'] = {d: device_replay(avail, d) for d in (0.0, 0.12, 0.3, 0.5)}
    out['needed'] = needed_delay(avail)
    nh = startup_no_hold(pcm, ends[0] if ends else None)
    first_end = ends[0] if ends else None
    held = [x for x in avail if first_end is None or x <= first_end + 0.05]
    out['startup_hold'] = (avail[0], [round(g, 2) for _, g in device_replay(held, 0.0)])
    out['startup_nohold'] = (nh[0] if nh else None, [round(g, 2) for _, g in device_replay(nh, 0.0)])
    return out


def main():
    runs = sorted(Path(sys.argv[1]).glob('*.json'))
    for p in runs:
        r = analyse(p)
        real = r['real_gaps']
        print(f"{r['run']:7s} tts_start {r['tts_start']:.2f}  first_pcm {r['first_pcm']:.2f}  release {r['first_release']:.2f}"
              f"  first_frame {r['first_frame']:.2f}  (no hold ~{r['first_frame_no_hold']:.2f})  first burst {r['burst_s']:.1f}s, next block +{r['next_after_burst']:.2f}s"
              f"  rtf first {r['rtf_first']:.2f} median {r['rtf_median']:.2f}  llm_done {r['llm_done']:.1f}")
        print(f"        gaps real: {len(real)} / {sum(real):.2f}s at {r['real_gap_at']}")
        for dl, gaps in r['delays'].items():
            print(f"        player waits {dl:.2f}s: {len(gaps)} gaps, {sum(g for _, g in gaps):.2f}s total")
        print(f"        start delay needed for zero gaps: {r['needed']:.2f}s")
        print(f"        first segment, with hold: first frame {r['startup_hold'][0]:.2f}s gaps {r['startup_hold'][1]} | no hold: first frame {r['startup_nohold'][0]:.2f}s gaps {r['startup_nohold'][1]}")
        for n in (5, 7, 9):
            print(f"        replay pb{n}: count {r[f'pb{n}'][0]}, total {r[f'pb{n}'][1]} s, worst {r[f'pb{n}'][2]} s, >=0.1 s at {r[f'pb{n}'][3]}")


if __name__ == '__main__':
    main()

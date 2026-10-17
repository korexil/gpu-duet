# PCM traces, 2026-10-16

Three answers each for two stages of the standalone pipeline, same text question and cloned voice,
on an RTX 4060 Laptop (8 GB), Windows 11: `gpu-*` = LLM and TTS both on the GPU, running at once;
`duet-*` = taking turns. Each JSON file is the `/events` replay of one answer. Besides the usual
`question`, `sentence`, `frame` (with the Pacer's gap estimate) and `llm_done`, the server recorded:

- `tts_start`: a sentence is handed to the TTS
- `pcm`: a PCM block arrives (`ms` = its duration)
- `chunk_end`: the TTS finished that sentence
- `release`: frames handed to the Pacer (`frames`), i.e. after the 1.2 s tail hold

`t` is wall-clock seconds. Analysis: `python bench/replay_trace.py bench/results/traces-2026-10-16`.

| | taking turns | at once |
|---|---|---|
| question → first sentence sent to TTS | 0.88–1.24 s | 0.72–0.93 s |
| TTS request → first PCM | 1.0–1.2 s | 2.3–2.5 s |
| first PCM burst | 1.0–1.2 s of audio, all at once | 1.0–1.2 s, all at once |
| first PCM → first frame sent (tail hold) | 0.19–0.20 s | 0.46–0.48 s |
| start delay a player would need for zero gaps (server-side timeline) | 0 / 0.02 / 0.12 s | 1.87 / 3.14 / 3.39 s |
| gaps reported | 0.01 s / 0.13 s / 0 | 3.40 s / 3.15 s / 1.87 s |

- **The tail hold delays the first frame.** The first PCM burst (1.0–1.2 s) never exceeds
  `tail_hold_ms` (1.2 s), so no frame goes out until the next block arrives, 0.19–0.20 s later
  when taking turns and 0.46–0.48 s when running at once.
- **One of the three turn-taking runs (duet-2) has an early gap of 0.13 s, and it comes from that
  hold.** When the next block arrived, only the excess over the hold, one 60 ms frame, was released,
  and the board ran dry before the block after that. The other two runs released several frames at
  that point and had 0.01 s and no gap. Replaying the first segment as if PCM could be sent on
  arrival, none of the three has a gap and the first frame is 0.19 s earlier. Only the first segment
  is replayed: after it, trimming the trailing silence changes the audio, which the replay can't
  model. With the hold removed the concurrent runs still have gaps.
- **Running at once, the TTS falls behind while the LLM is generating.** All gaps fall in the first
  13 s, while the LLM is generating or just after it finishes (10.5–12.3 s); none come later in the
  64–73 s answers. The start delay needed for zero gaps is set by the latest frame in that window.
  *Correction (2026-10-18):* the per-sentence RTF figures first given here (first sentence 1.49–1.51
  against 0.96–0.98 when taking turns; median 0.84–0.89 either way) were computed from `pcm` events
  that the server published when its event loop took each block off the queue, and after the first
  frame that loop runs at playback pace, so blocks that arrived early were stamped late. Measured at
  arrival ([traces-2026-10-18](../traces-2026-10-18/)): first sentence 1.33–1.47 running at once
  against 0.61–0.62 when taking turns; later sentences about 0.6 either way.
- **Checking the replay.** Replaying the Pacer on the recorded release times with its real 5-frame
  prebuffer gives the same number of gaps as the server reported in every run, totals within 0.011 s,
  timestamps within a few milliseconds (one gap of 0.090 s is replayed as 0.101 s). With 7 or 9
  frames the result is identical by construction, not by measurement: the prebuffer only limits how
  far ahead the server sends, and the gap estimate assumes playback starts with the first frame. So
  this replay can't say whether a bigger prebuffer helps on the board; what it can vary is how long
  a player waits before starting (the table row above). That is a lower bound on the server's
  timeline: it assumes the board plays at exactly 1x with no jitter and doesn't include decoding.
- "First frame" is the server sending its first audio frame; the board's own buffering and decoding
  come after that and are not measured. Gaps are the server-side estimate, not measured at the
  speaker.

## Trying it on another card

The useful comparison is whether, with the LLM and TTS running at once, PCM still arrives before the
playback buffer runs out. If you run both stages on a different setup, these fields make results
comparable (format suggested on the Hugging Face forum):

```
GPU / VRAM / power limit / driver / OS:
LLM model + quantization + llama.cpp revision:
TTS engine + model revision + voice prompt:
Dedicated/shared GPU memory during the run:
Cold/warm policy; text hash; answer audio duration:
Playback model / initial buffer / metric definitions:

mode    first-audio-s   text-done-s   gap-total-s   worst-gap-s   start-delay-for-zero-gaps-s
plain   ...
turns   ...
```

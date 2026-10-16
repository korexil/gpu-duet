[中文](benchmarks.md) · English

# Measured data

Environment: RTX 4060 Laptop 8 GB, 16 GB RAM, Windows 11. The two sets of data below use different setups: the first runs the full standalone-server pipeline and shows the effect of each step from the starting point to now; the second differs only in scheduling and is used to tell which technique actually works.

First audio = from the question being sent to the server sending the first audio frame (four stages: the moment the server sends the first Opus frame to the board; controlled experiment: the moment a simulated player has buffered 0.5 s and starts playing). The four-stage first audio includes the server's tail hold: the last `tail_hold_ms` (default 1200 ms) of each segment's audio is held back so the trailing silence of the segment can be trimmed, so the first frame is sent only after more than 1.2 s of audio has arrived (or the first segment has ended). The board still has to buffer and decode before any sound comes out; that part was not measured. Gaps = the sum of the time the estimated playback buffer ran empty (four stages: estimated by the server against the device's 0.3 s prebuffer; controlled experiment: a simulated player); neither was measured directly at the speaker.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="img/before-after-dark.svg">
  <img alt="Four metrics, before → after. First audio on a long answer 4.4 → 2.3 s and total gaps 4.59 → 0.09 s: the average of three runs each of the starting stage and the turn-taking stage in the four-stage measurements; loudness spread between sentences 4.6 → 3.7 dB: per-sentence shaping on the benchmark answers; name wake-up 29 → 53/60: best on-board MultiNet result vs. server-side SenseVoice" src="img/before-after-light.svg" width="480">
</picture>

## Four stages: same question, first audio and gaps drop stage by stage

All four stages were measured in one sitting on 2026-10-16 (00:59–01:37), on the same standalone-server pipeline, with the same cloned voice and the same question, "讲一个关于蓝色鹦鹉和月亮的故事，三百字左右。" ("Tell a story about a blue parrot and the moon, about 300 characters."). Each stage got one warm-up question (not counted), then three recorded runs. Gaps are estimated against the device's 0.3 s prebuffer (`Pacer` in [`standalone/session.py`](../standalone/session.py)). Before each question we waited 10 s to let the GPU downclock. A record may contain only one answer, otherwise it is discarded on the spot and re-measured (all 12 runs this time were valid). The warm-up run of each stage is not in the records. The measurement order was 1, 0, 2, 3 (not by stage number), with no randomization. Questions were sent to the server as text, so first audio excludes VAD and ASR. A gap is counted whenever the playback buffer runs empty, even for 0.01 s, so the "count" is very sensitive to tiny gaps; the total is more reliable. Raw per-run records (including each sentence's text and the times at which gaps of ≥ 0.1 s occurred; shorter ones only count toward the number and total): [bench/results/stages-2026-10-16.txt](../bench/results/stages-2026-10-16.txt).

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="img/stages-dark.svg">
  <img alt="First audio and total gaps for three runs of each of the four stages. Starting point: 6.00 / 3.49 / 3.85 s, gaps 3.91 s (48) / 5.10 s (60) / 4.75 s (54); new engine: 5.42 / 5.21 / 5.34 s, gaps 0.14 s (2) / 0 / 0.01 s (1); LLM on GPU: 3.82 / 3.78 / 4.07 s, gaps 2.85 s (14) / 2.58 s (9) / 3.08 s (17); taking turns: 2.67 / 1.87 / 2.25 s, gaps 0 / 0.13 s (1) / 0.14 s (1)" src="img/stages-light.svg" width="480">
</picture>

| Stage | TTS | LLM | Scheduling | First audio s | Total gaps s (count) |
|---|---|---|---|---|---|
| 0 Starting point | PyTorch version, GPU | CPU (`-ngl 0`) | At the same time | 6.00 · 3.49 · 3.85 | 3.91 (48) · 5.10 (60) · 4.75 (54) |
| 1 New engine | This repo's native engine, GPU | CPU | At the same time | 5.42 · 5.21 · 5.34 | 0.14 (2) · 0 (0) · 0.01 (1) |
| 2 LLM on GPU | Native engine, GPU | GPU (`-ngl 99`) | At the same time (`llm.overlap: true`) | 3.82 · 3.78 · 4.07 | 2.85 (14) · 2.58 (9) · 3.08 (17) |
| 3 Taking turns | Native engine, GPU | GPU | Generate the next segment after each segment is synthesized | **2.67 · 1.87 · 2.25** | **0 (0) · 0.13 (1) · 0.14 (1)** |

- In stage 0, each run had 48–60 gaps totaling 3.9–5.1 s, most of them shorter than 0.1 s; there were 8–12 gaps of ≥ 0.1 s per run, the longest 0.78 s, all within roughly the first 35 s of the answer (while the LLM was still writing). The PyTorch TTS process had 8.9 GB of private memory, most of it swapped to the page file on a 16 GB machine ([pitfall #13](pitfalls.en.md), recorded 10-14). First audio in this stage varies a lot between the three runs (3.49–6.00 s); the two 10-14 runs (6.67 and 5.81 s) both fell at the high end.
- The 0.13–0.14 s of gaps left in stage 3 occurred about 0.2 s after the first sound (at 2.06 s and 2.45 s after the question), and after that the rest of the answer had no more gaps.
- Time for the LLM to finish writing all the text: 10.7–13.8 s in stage 2, 50.2–59.7 s in stage 3; the answer audio is 65–84 s. The price of taking turns is that the LLM spends most of its time waiting for the TTS.
- The README's "before / after" is the average of three runs of stage 0 and stage 3: first audio (6.00 + 3.49 + 3.85) / 3 = 4.45 ≈ 4.4 s → (2.67 + 1.87 + 2.25) / 3 = 2.26 ≈ 2.3 s; gaps (3.91 + 5.10 + 4.75) / 3 = 4.587 ≈ 4.59 s → (0 + 0.13 + 0.14) / 3 = 0.09 s.
- Each stage has only three runs, enough to see the order of magnitude; for statistics that can be compared, see the next section.
- The first measurement on 2026-10-14 (two runs per stage) gave first audio 6.67 / 5.81 s and gaps 3.68 / 4.75 s for the starting point, and first audio 2.09 / 2.12 s and gaps 0.13 / 0.20 s for taking turns; in the new-engine stage one record had two answers mixed into it (first audio 5.18 s, gaps 55.89 s) and was discarded. That measurement script had no "one answer per record" check, and the original question text wasn't recorded either, so the whole set was re-measured, and this one is authoritative.

## Benchmark: only GPU turn-taking brings gaps down to 0

A 2×2 controlled experiment that separates the two factors "GPU turn-taking" and "shorter sentences", 6 runs per cell, medians (min–max in parentheses). For the same question and seed, the generated text is word-for-word identical in both modes (12/12 pairs), so the difference comes only from scheduling. It doesn't go through a device; [`tools/turn_taking_sim.py`](../tools/turn_taking_sim.py) simulates a player with a 0.5 s buffer. Method, reproduction commands and raw data: [bench/](../bench/README.md).

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="img/bench-dark.svg">
  <img alt="Median first audio and total gaps for four cases: no scheduling 3.20 s / 2.27 s; shorter sentences only 2.62 s / 0.48 s; GPU turn-taking only 1.83 s / 0; shorter sentences + GPU turn-taking 1.42 s / 0" src="img/bench-light.svg" width="480">
</picture>

| Case | Median sentence length | First audio s | Total gaps s | Runs with gaps | Audio length s |
|---|---|---|---|---|---|
| No scheduling | 40 characters | 3.20 (2.89–3.53) | 2.27 (1.39–5.12) | 6/6 | 81 (67–118) |
| Shorter sentences only (0.9 s pause between sentences) | 13.5 characters | 2.62 (2.06–2.68) | 0.48 (0–1.55) | 4/6 | 45 (26–80) |
| GPU turn-taking only | 40 characters | 1.83 (1.49–1.92) | 0 | 0/6 | 81 (67–118) |
| Shorter sentences + GPU turn-taking | 13.5 characters | 1.42 (1.29–1.64) | 0 | 0/6 | 45 (26–80) |

With shorter sentences alone, whether gaps appear depends on how long the answer is; see [pitfall #9](pitfalls.en.md).

## PCM trace: where first audio goes and where gaps come from

On 2026-10-16, three runs each of stage 2 (both using the GPU at the same time) and stage 3 (taking turns) from the four stages, with the same question and voice. The server additionally recorded in the event stream when each sentence starts synthesizing (`tts_start`), each PCM chunk's arrival and duration (`pcm`), and the number of frames handed to the Pacer (`release`, after the tail hold). Raw events and the analysis script: [bench/results/traces-2026-10-16/](../bench/results/traces-2026-10-16/), [bench/replay_trace.py](../bench/replay_trace.py).

| | Taking turns | At the same time |
|---|---|---|
| End of question → first sentence starts synthesizing | 0.88–1.24 s | 0.72–0.93 s |
| Synthesis start → first PCM batch | 1.0–1.2 s | 2.3–2.5 s |
| Length of the first PCM batch | 1.0–1.2 s of audio, arriving all at once | 1.0–1.2 s, arriving all at once |
| First PCM batch → first frame sent (tail hold) | 0.19–0.20 s | 0.46–0.48 s |
| How much audio the board must buffer first to play through without a gap (server timeline) | 0 / 0.02 / 0.12 s | 1.87 / 3.14 / 3.39 s |
| Total gaps reported | 0.01 s / 0.13 s / 0 | 3.40 s / 3.15 s / 1.87 s |

- **The tail hold delays the first frame.** The first PCM batch (1.0–1.2 s) is no longer than `tail_hold_ms` (1.2 s), so the first frame is sent only after the next chunk arrives: 0.19–0.20 s of extra wait when taking turns, 0.46–0.48 s when running at the same time.
- **In one of the three taking-turns runs (duet-2) there was a 0.13 s gap at the start, caused by exactly this hold.** When the next chunk arrived, only the part beyond the hold was released, one frame (60 ms), and by the time the board finished playing it the next chunk hadn't arrived. In the other two runs several frames were released at that moment, giving gaps of 0.01 s and 0. Replaying the first segment as if "PCM can be sent as soon as it arrives", none of the three runs has a gap, and the first frame comes 0.19 s earlier. Only the first segment is replayed: trimming trailing silence afterwards changes the audio length, which can't be simulated here. With the hold removed, the three at-the-same-time runs still have gaps.
- **When running at the same time, the TTS can't keep up while the LLM is generating.** The first sentence's wall-clock RTF (from request to last PCM chunk) is 1.49–1.51, against 0.96–0.98 when taking turns. All the gaps fall within the first 13 s, which is while the LLM is still generating or has just finished writing (10.5–12.3 s); in the 64–73 s answers there are no more gaps after that, and the median per-sentence RTF over the whole answer is the same as when taking turns (0.84–0.89). The wait needed to play through without a gap is set by the latest frame in this stretch.
- **Checking the replay.** Replaying the Pacer with the recorded release times and the server's actual 5-frame prebuffer, the number of gaps in each run matches what was reported, the total differs by at most 0.011 s, and the timestamps differ by a few milliseconds (one 0.090 s gap replayed as 0.101 s). Changing it to 7 or 9 frames gives exactly the same result; that is determined by the algorithm itself, not measured: the prebuffer only limits how many frames the server can send ahead, and the gap estimate assumes the board starts playing as soon as it receives the first frame. So this replay can't say whether a larger prebuffer would help on the board; the only thing that can change is "how long the board waits before starting to play" (that row in the table). This is a lower bound on the server timeline: it assumes the board plays at exactly 1× speed with no jitter, and it doesn't include decoding.
- When taking turns, the LLM finishes writing all the text at 49.5–56.9 s, against 10.5–12.3 s when running at the same time.

## Other metrics

| Metric | Value | Source |
|---|---|---|
| Total VRAM | about 7.3 GB / 8 GB (7,448 / 8,188 MiB; TTS 3.9–4.0 GB, LLM about 2.8 GB, desktop etc. 0.4–0.6 GB); about 8.3 GB estimated without the VRAM-saving patches (7,448 + 488 + 594 MiB) | "Reproducing" below; [architecture.md](architecture.md#资源预算实测) (Chinese), [how-it-works.en.md](how-it-works.en.md#1-saving-vram-native-c-tts-engine--patches-tts-from-about-50-gb-down-to-about-40-gb) |
| TTS real-time factor (running alone) | about 0.6 (self-built 0.58–0.61, prebuilt package 0.62–0.70); 1.1–1.6 for the first sentence while the LLM is decoding | [native-tts/README.md](../native-tts/README.md) (Chinese), [pitfall #9](pitfalls.en.md) |
| LLM decoding / first token | 61–66 tok/s / 0.1–0.2 s (depending on prompt length) | [pitfall #4](pitfalls.en.md), "Reproducing" below |
| End-to-end first audio, via xiaozhi-esp32-server | 1.0–1.8 s with text input; 2.0–2.9 s with voice input (counted from the end of speech, including VAD + ASR) | [architecture.md](architecture.md#延迟用法-c软件链路从发出文字到收到第一个-opus-包) (Chinese) |
| Loudness spread between sentences | 4.6 → 3.7 dB (per-sentence shaping) | [`leveler.py`](../tts-service/leveler.py) |
| Loudness fluctuation in 0.25 s windows (p90-p10) | 5.8–7.5 → 4.7–6.0 dB (standalone's AGC, three fixed answers) | `python tools/agc_check.py` (reproduced 2026-10-14: 7.2 → 4.9, 7.5 → 6.0, 5.8 → 4.7 dB for the three) |
| Name wake-up (Japanese name, 60 clips) | On-board MultiNet best 29/60; server-side SenseVoice 53/60, false wakes 0/20 | [pitfall #20](pitfalls.en.md) |
| GPU idle downclocking | After 8 s idle, the TTS's first block takes 0.64 s, against 0.17 s when warm | [pitfall #11](pitfalls.en.md) |

### Reproducing

VRAM (with tts-service and llama-server both running and idle, PowerShell). GB follows Task Manager's convention, 1 GB = 1024 MiB:

```powershell
nvidia-smi --query-gpu=memory.used,memory.total --format=csv
(Get-Counter '\GPU Process Memory(*)\Dedicated Usage').CounterSamples |
    Where-Object CookedValue -gt 100MB | Select-Object InstanceName, @{n='MiB'; e={[int]($_.CookedValue / 1MB)}}
```

Measured on 2026-10-14: total 7,448 / 8,188 MiB; tts-service 4,027–4,065 MiB (varies with the length of the sentence just synthesized), llama-server 2,899 MiB, other processes about 480 MiB.

LLM decoding speed and first token: `timings.predicted_per_second` and `timings.prompt_ms` returned by `/completion`.

```bash
curl -s http://127.0.0.1:18080/completion -H "Content-Type: application/json" \
  -d '{"prompt": "Tell me a long story about autumn.", "n_predict": 200, "cache_prompt": false}'
```

Reproduced 10 times on 2026-10-14 (48–400 tokens generated, 36–408 token prompts): decoding 61.0–63.9 tok/s, and 57–84 ms for a 36-token prompt and 164–177 ms for a 408-token prompt. Earlier records were 63–65 tok/s ([pitfall #4](pitfalls.en.md)) and 66 tok/s (before the VRAM overflow in [pitfall #12](pitfalls.en.md)); the docs consistently say 61–66 tok/s.

The figures are generated by [`img/make_figures.py`](img/make_figures.py).

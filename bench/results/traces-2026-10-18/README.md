# PCM traces, 2026-10-18 (v0.1.3: no tail hold, events stamped at arrival)

Same setup and question as [traces-2026-10-16](../traces-2026-10-16/): the standalone pipeline on an
RTX 4060 Laptop (8 GB), Windows 11, three answers each for `duet-*` (taking turns) and `gpu-*` (LLM and
TTS both on the GPU at once), all six in one sitting. Two things changed in the server since 10-16:

- **No tail hold.** The server now asks tts-service for exactly `chunk_pause_ms` (0.4 s) of silence
  after each sentence (the request's `pause` field) and forwards every block as it arrives, instead of
  holding back 1.2 s to trim the pause itself. The first frame goes out with the first PCM block.
- **Trace events are stamped when things happen.** `tts_start`, `pcm` and `chunk_end` are now
  published from the worker thread at request / arrival / end time. On 10-16 they were published when
  the event loop took each item off the queue, and after the first frame that loop runs at playback
  pace, so blocks that arrived early were stamped late. The 10-16 per-sentence RTF figures are
  therefore overstated; the numbers below replace them.

`replay.txt` is the output of `python bench/replay_trace.py bench/results/traces-2026-10-18`.

| | taking turns | at once |
|---|---|---|
| question → first sentence sent to TTS | 0.81–0.95 s | 0.57–1.14 s |
| TTS request → first PCM | 0.96–1.01 s | 1.9–2.9 s |
| first PCM burst | 0.8–1.0 s of audio | 0.9–1.1 s |
| first PCM → first frame sent | 0 (same block) | 0 |
| first frame sent | 1.77 / 1.83 / 1.96 s | 2.48 / 3.39 / 4.03 s |
| first sentence RTF (request → its last PCM, ÷ audio) | 0.61 / 0.62 / 0.61 | 1.47 / 1.46 / 1.33 |
| per-sentence median RTF over the answer | 0.61–0.65 | 0.62–0.65 |
| gaps reported | 0 / 0 / 0 | 2.36 s (9) / 3.06 s (7) / 1.59 s (6) |
| start delay a player would need for zero gaps (server timeline) | 0 / 0 / 0 | 2.35 / 3.07 / 1.59 s |
| LLM finished writing | 47–54 s | 11.3–12.0 s |

- **Taking turns, the early gap is gone and the first frame is 0.19 s sooner.** On 10-16 the first
  frame waited for the second PCM block (the first burst never exceeded the 1.2 s hold) and one of
  three runs stalled 0.13 s right after it. Now the first frame leaves with the first block and all
  three runs play through without a gap. The first-frame ranges in the table are different runs
  (three each, with the LLM's own start-up variation in them); the paired evidence for the 0.19 s is
  the no-hold replay in traces-2026-10-16 and the A/B below.
- **Running at once, the TTS falls behind only while the LLM is decoding.** The first sentence takes
  1.3–1.5× its own length to synthesize (0.6× when taking turns); all gaps fall within the first
  13 s, while the LLM is still writing or has just finished (11.3–12.0 s), and the per-sentence RTF
  over the rest of the answer is the same 0.6 either way. The 10-16 figures of 0.96–0.98 and
  0.84–0.89 are most likely the dequeue artefact described above; the hold and the pause request also
  changed between the two days, so this is the best explanation, not a controlled comparison.
- **Removing the hold didn't change the pauses, as far as one comparison shows.** The same question
  at temperature 0, twice through each server (`ab/`): the pair with identical answer text has the
  same pause and silence statistics (about 0.45 s at the join, 0.37 s lead-in, 0.42 s trailing;
  `ab/stats.txt`, silence = below 300 of 32767) and the first audio 0.2 s sooner (1.52 s against
  1.72 s on the device's clock). The other run through the new server got different text from the LLM
  (temperature 0 doesn't guarantee identical text, pitfall #25) and can't be compared. One question,
  one matched pair; one listener heard no difference between the two in a blind comparison. The
  wavs are not in the repo.
- Interrupting an answer and TTS failures are in [interrupt-2026-10-18](../interrupt-2026-10-18/).
- Same caveats as before: "first frame" is the server sending it, the board's buffering and decoding
  are not included; gaps are the server's estimate against the device's 0.3 s prebuffer, not measured
  at the speaker; three runs per setup.

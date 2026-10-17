# Interrupt fixture, 2026-10-18

What happens when the board's button interrupts an answer and a new question follows at once, with
the standalone server of v0.1.3 (no tail hold, generation ids on events, 10 s TTS read timeout).
Produced by `bench/interrupt_fixture.py` through `bench/tts_fault_proxy.py` (the server's
`tts.base_url` pointed at the proxy for all three runs, including the clean one), RTX 4060 Laptop,
Windows 11, taking turns, same question A (a 300-character story) every time:

1. question A, sent as device text;
2. an `abort` (what the button sends) 2 s after A's first audio frame;
3. question B right away; with `--fault`, the proxy was armed just before B so B's first `/stream`
   got a 500 (`error`) or no bytes at all (`hang`).

Each JSON file holds the summary and every `/events` event of the run (`gen` = answer number). One run per column.

| | clean | TTS 500 on B | TTS hang on B |
|---|---|---|---|
| events of A after B's question | none | none | none |
| A's last frame before B's question | 3 ms | 17 ms | 4 ms |
| frames the device received after the abort | 0 | 0 | 0 |
| B: first sentence to the TTS | 1.06 s | 1.09 s | 1.92 s |
| B: TTS request → first audio block | 0.98 s | (500) | (no bytes) |
| B: first frame sent | 2.04 s | — | — |
| B: answer ended (`stop`) | 8.54 s, 108 frames, no gaps | 1.11 s, 0 frames | 11.95 s, 0 frames |

- **Nothing of the old answer crosses into the new one.** The abort cancels A's task; the worker
  thread stops at its next check; A's `stop` goes out within a few milliseconds and no event tagged
  with A's generation appears after B's question. The device got no audio after the abort.
- **The TTS slot was free for B.** B's first request got its first block after 0.98 s, the same as a
  request on an idle TTS (1.0–1.2 s on 2026-10-16), so A's cancelled sentence didn't keep the TTS
  busy. In this run the abort came while A's TTS was streaming (2 s into the audio), so the worker
  noticed the cancel at the next 100 ms block and closed the request. An abort that lands before the
  first block of a sentence would keep the TTS busy until that block arrives (up to the time to first
  audio), or, with a hung TTS, until the 10 s read timeout; neither case is in these runs.
- **A TTS error ends the answer at once**: B's `stop` came 25 ms after the failed request. The device
  is told the error text as the sentence (so it shows on the screen) and gets no audio.
- **A hung TTS is cut off by the read timeout**: no bytes for 10 s (`tts.read_timeout_s`) → the answer
  ends, 11.95 s after the question. Before v0.1.3 the limit was 60 s.
- "Last frame", "first frame" and `stop` are server-side times from the event bus; the board's own
  buffering is not included. B's time to its first sentence (1.06 / 1.09 / 1.92 s) is the LLM writing
  it and varies with the answer.

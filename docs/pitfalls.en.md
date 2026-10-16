[中文](pitfalls.md) · English

# Pitfalls

Environment: RTX 4060 Laptop 8 GB, 16 GB RAM, Windows 11; device (the "board" below) M5Stack Atom EchoS3R (ESP32-S3); server xiaozhi-esp32-server (Docker). The conversation language is Japanese, but most conclusions don't depend on the language.

---

## Part 1 · "The model got dumber": check the input before swapping the model

**1. The system prompt itself was causing the symptoms.** The prompt said "keep answers short", "ask back if unsure" and "in an emergency, contact someone else" (quoted in translation; the prompt was not in English); the result was short answers, follow-up questions that killed the conversation, and advice to seek help even when asked about its own matters. Swapping in several different models changed nothing. Read the system prompt first.

**2. ASR JSON ended up verbatim in the chat history.** FunASR returns `{"content","language","emotion"}`; without speaker recognition enabled, the whole JSON string was treated as the user's message. Fix: [#3411](https://github.com/xinnan-tech/xiaozhi-esp32-server/pull/3411).

**3. SenseVoice puts spaces between words when transcribing Japanese** (`今日 の こと を`). Fix: [#3412](https://github.com/xinnan-tech/xiaozhi-esp32-server/pull/3412).

**4. A 4B model on the CPU with temperature 1.0 and a 2048 context is just weak.** Switching to Gemma 4 E4B (QAT q4_0) with all layers on the GPU gives 61–66 tok/s decoding and 0.1–0.2 s to the first token (depending on prompt length: about 0.1 s for 36 tokens, about 0.2 s for 400 tokens; how to reproduce: [benchmarks.en.md](benchmarks.en.md#reproducing)), and clearly better quality with the same prompt.

## Part 2 · Prompts for small models

**5. Examples in the prompt get copied over and over.** The prompt listed a few everyday topics to talk about (the window, the curtains, the train...); in 20 runs of "tell me about your day", "window" came up in all 20. With the examples removed: 0.

**6. The longer the sentence, the less headroom the TTS has.** On the conversation set from "Evaluation method" below, without constraints the median sentence length was 19 characters and the longest 53. With the prompt saying "one thing per sentence, about 15 characters, break with a period promptly" (Japanese in the original, translated here), this dropped to 12 / 27 characters (the benchmark questions ask for a longer answer; with the same two prompts the figures are 40 / 13.5 characters, see [benchmarks.en.md](benchmarks.en.md#benchmark-only-gpu-turn-taking-brings-gaps-down-to-0)). Each TTS call gets shorter and there are fewer gaps, but they don't go away: in the benchmark, shortening sentences alone still left gaps in 4 of 6 runs (item 9).

**7. A small model ends about 75% of its answers with a question.** The prompt alone can't suppress it, and it fights with other requirements. If you don't want that, when the previous turn ended with a question, append a note to the **end of the last user message** (the provider's `question_rest_note`). Put it at the end rather than in the system prompt so the prefix KV cache isn't affected. Measured: down to 9–26%, and the note was read aloud 0/66 times.

**8. "Don't use symbols" gets understood as no punctuation either.** The whole sentence came out without a period, and the TTS sentence splitting and pauses fell apart. Write "use punctuation as usual".

**Don't put these in the prompt:**
- "Keep answers short" / "ask back if unsure": the model does exactly that; answers get short and the follow-up questions kill the conversation.
- Topic examples, sample answers: they get copied over and over.
- "Don't use symbols": punctuation gets dropped along with them.
- "End every turn with a question" / "don't always ask questions": the former asks every time, the latter can't suppress it; use `question_rest_note`.

**Evaluation method:** don't look at single sentences. Use about 5 sets of real conversations with 4–5 turns each (greetings, "tell me more", "keep going", feeling low, remembering names), run a few random seeds per set, and count: the share of answers ending in a question, sentence length (median / max), how many times the same topic repeats, and how many times something you banned in the prompt shows up.

## Part 3 · Running an LLM and a TTS on one 8 GB GPU

**9. Run at the same time, the TTS can't keep up with real time.** The TTS alone has an RTF of about 0.6; while the LLM is decoding, the first sentence's RTF rises to 1.1–1.6 and playback breaks up right at the start.
- Throttling the LLM (20 or 14 tok/s): no effect, RTF still about 1.4. Suspected cause: the TTS decodes frame by frame as a large number of very small synchronizing kernels, so even if the LLM only takes a small slice of GPU time, the TTS waits on every step (not verified with a profiler).
- Changing the WDDM GPU scheduling priority (`D3DKMTSetProcessSchedulingPriorityClass`): no effect on CUDA compute.
- **Take turns on the GPU**: the LLM stops at the end of a sentence and continues only once the TTS `/health` is no longer `busy`. [Benchmark](../bench/README.md) (6 runs per cell, medians):

  | | First audio | Total gaps | Runs with gaps |
  |---|---|---|---|
  | No scheduling | 3.20 s | 2.27 s | 6/6 |
  | Shorter sentences only (+0.9 s pause) | 2.62 s | 0.48 s | 4/6 |
  | GPU turn-taking only | 1.83 s | 0 | 0/6 |
  | Shorter sentences + GPU turn-taking | 1.42 s | 0 | 0/6 |

  An early single measurement suggested "with shorter sentences and added pauses there are no gaps even without turn-taking"; after 6 repeats that doesn't hold. Whether gaps appear depends on how long the answer is: with shorter sentences only, the 2 runs with audio up to 34 s had no gaps, and all 4 runs with audio of 35 s or more had gaps (0.45–1.55 s). The LLM keeps generating in the background, so the longer the answer, the longer the TTS is slowed down, and the headroom built up at the start eventually runs out. That early test was a single run and the answer length wasn't recorded; it very likely hit a short answer. Implementation: `server/core/providers/llm/llamacpp_paced/`; measurement: `bench/run.py`.

**10. Don't use the chat endpoint's assistant prefill to continue in chunks.** The template strips trailing whitespace from the prefill, so continuing doesn't match generating in one go. Use `/completion` instead, with the prompt as `[rendered template string, *already generated token ids]`: the prefix matches token for token and the KV cache hits. When `stop_type == "eos"`, the final end token gets restored by `/detokenize` as text like `<turn|>`; strip it.

**11. GPU idle downclocking slows down the first sentence.** After 8 s idle the GPU sits at P8 (210 MHz) and the TTS needs 0.64 s for its first block; warm, at P0, it takes only 0.17 s.

**12. When VRAM is oversubscribed, the Windows driver doesn't report an error; it just gets slower.** With two TTS instances on the GPU at once, most of llama-server's VRAM was moved to shared system memory and decoding dropped from 66 to 45 tok/s, with no crash. To check: look at each process's "Dedicated GPU memory" in Task Manager. Restarting the affected process puts it back in VRAM.

**13. RAM needs budgeting too (16 GB).** The PyTorch TTS process used 8.9 GB of private memory, most of it swapped out to the page file and read back from disk on every synthesis: slow and stuttery. After switching to the native C++ engine (`native-tts/`), VRAM is about 4.0 GB and RAM is much smaller. Also, Ollama starts llama-server with `--load-mode none`, so the weights are read into private memory rather than mmapped (4.7 GB seen); Docker Desktop's WSL VM takes about 3.4 GB. With all three running, available memory was pushed down to 141 MB, with about 60,000 pages swapped per second.

## Part 4 · Sound after per-sentence synthesis

**14. TTS volume jumps between sentences** (a 5–7 dB spread within one answer). Set one gain per sentence from its first 0.6 s and keep it for the whole sentence. Two approaches that don't work:
- Adjusting gain every 0.32 s within a sentence: it fights the sentence's own stress and sounds like it's twitching.
- Reusing the previous sentence's gain: when the previous sentence was quiet and this one starts loud, the first syllable gets amplified into a pop.

**15. Pauses vary in length** (0.2–1.25 s). The model puts 0.4–0.7 s of leading silence on every sentence, and trailing silence varies too. Cut the leading silence and pad the end to a fixed length. The xiaozhi server strips the trailing punctuation of each segment before handing it to the TTS, so pauses can only be told apart by sentence length.

**16. Crackling or distorted audio on a small speaker.** tanh soft clipping distorts on a small speaker; the Opus codec can also push a -0.4 dB peak up to full scale. Use a smooth limiter and keep peaks at -6 dBFS; if it isn't loud enough, raise the device speaker volume instead of pushing the digital signal.

**17. A "pop" every time before it speaks.** The device turns its amplifier off when idle and powers it on when it starts speaking. The model's own leading silence used to cover this; after cutting it, the pop is exposed. Fix: for the first sentence after more than 1.5 s of silence, leave 0.3 s of silence in front.

**18. Setting the speaker volume from the server**: MCP tool `self.audio_speaker.set_volume`; the firmware default is 70.

**19. Sentence splitting cuts through quotation marks** (`「わぁ！」って` got split into two segments). Fix: [#3413](https://github.com/xinnan-tech/xiaozhi-esp32-server/pull/3413).

## Part 5 · Wake words (names that aren't Chinese or English)

**20. A custom wake word (a Japanese name) is rarely detected by ESP-SR MultiNet when approximated with pinyin.** How it was tested: 60 clips synthesized with 10 VOICEVOX voices, played from the computer's speakers for the board, hits counted from the serial port (`tools/wake_test/`).

| Setup | Hits | False wakes (20 confusable phrases) |
|---|---|---|
| MultiNet5 (Chinese, quantized), 5 pinyin entries, threshold 0.2 | 3/60 | 0/20 |
| Same, 24 pinyin variants, threshold 0.06 | 29/60 | 1/20 |
| Same, 35 entries, threshold 0.06 | 28/60 | 2/20 |
| MultiNet5, threshold 0.01 | 9/60 | 0/20 |
| MultiNet7 (Chinese), 36 entries, threshold 0.08 / 0.04 | 20/60 / 18/60 | 0/20 |
| **Server-side SenseVoice on the same audio** | **53/60** | **0/20** |

Hit rate isn't monotonic in the threshold, and the same audio gives different results from run to run. The pinyin that hits most often is what the Chinese model "hears" (for example, Japanese 「の」 is heard as nao), not a spelling built from the pronunciation.

**21. So do wake-word detection on the server** (`server/core/handle/wakeGate.py`): the board keeps one always-on listening session, every utterance goes through ASR on the server, and the server only responds when it hears the name.
- ASR often transcribes the name as **homophone characters**, so `wake_patterns` needs the homophone spellings and common misrecognitions.
- Upstream has a secondary close on "no-voice timeout" that would drop the always-on session; the patch refreshes the activity time while the gate is open.
- Other speech in the room is also recognized, just not responded to. CPU cost is about 130 ms per utterance.

**22. To use a larger MultiNet, the assets partition (2.25 MB) is too small.** If you don't use OTA, you can drop `ota_1` and grow assets to about 5 MB, leaving `ota_0` alone. First read otadata with esptool to confirm the board currently boots from `ota_0`. The wake configuration is in `index.json` in the assets partition, so only this partition needs reflashing, not a firmware rebuild (`tools/esp32/xiaozhi_assets.py`).

**23. Opening the serial port resets the ESP32: set DTR and RTS to False first**, or the board will reset.

## Part 6 · Building

**24. nvcc on Windows hits the 260-character path limit**, with the error `Could not open output file ...cu.obj.d`. Use a short build directory.

## Part 7 · Measuring

**25. Same seed, not always the same output on llama-server.** The cached prefix length in the slot differs, so the batch shape differs, the logits differ slightly, and sampling diverges from some token on. Measured with the same seed three times in a row: the first run differed from the next two. Sending an unrelated short `cache_prompt: false` request first to push the cache out made all three identical. Do this for A/B comparisons (`bench/run.py`).

**26. When measuring, first make sure the only processes on the GPU are the ones you're measuring.** A watchdog task restarted an old TTS in the middle of a test; two TTS instances held the GPU at once and synthesis froze; after that, llama-server's weights were moved to shared memory (item 12) and didn't come back on their own after the extra process was stopped, so llama-server had to be restarted. Check each process's "Dedicated GPU memory" in Task Manager.

## Part 8 · Install scripts (Windows)

**27. In Windows PowerShell 5.1, a script exits with an error as soon as its output is redirected.** With `$ErrorActionPreference = 'Stop'`, ordinary progress messages that git, cmake, pip and `vcvars64.bat` write to stderr (such as `Cloning into...`) are treated as fatal errors. Running directly in a terminal is fine, but saving a log with `*> build.log` fails at the clone step. Fix: set it to `Continue` temporarily when calling external programs and look only at the exit code (`Invoke-Native` in `native-tts/build.ps1`). Related: if a clone fails halfway, the directory already exists, and the next run treats the half-cloned source as done; so a completion marker is now written only after the patch has been applied.

**28. After `git clone --filter=blob:none`, `git checkout <commit> -- scripts` fails** with `unable to read sha1 file`; the partial clone doesn't fetch file contents on demand. When you only need a few files from a pinned commit, use `git init` + `git fetch --depth 1 <url> <full SHA>` + `git checkout FETCH_HEAD -- scripts` (`native-tts/setup-models.ps1`).

**29. Putting the venv in a directory that is too deep makes the torch install fail**: `OSError: [WinError 206] 文件名或扩展名太长` (on English Windows: `The filename or extension is too long`). torch's dependencies contain very long test file paths, which together with the venv's own path exceed 260 characters. Put the venv under a short path (for example a `venv` directory at the drive root), or enable long path support in the system.

## Part 9 · The C interface (Python ↔ C++)

**30. Comparing only `sizeof` doesn't verify the ABI.** Swapping two fields of the same width in a struct, or changing an `int32` to a `float`, leaves the size unchanged, so the check still passes, and then memory is read at the wrong offsets. The bridge library now exports two tables: each struct's size plus **each field's offset** (catches reordering, additions, removals), and each field's **type code** (catches a type change at the same width); Python computes the same two tables from ctypes `.offset` and field types and compares them entry by entry (`qwen3_tts_bridge_abi_layout` / `qwen3_tts_bridge_abi_types` in the `native-tts/` patch, `check_abi` in `tts-service/native_engine.py`). CI builds the CPU version once on Linux and once on Windows, then runs this comparison.

**31. A C++ exception crossing the C boundary kills the host process outright.** An exception thrown inside an `extern "C"` function has nobody to catch it; on Windows the whole Python process exits without a single line of error. Every exported function of the bridge library is wrapped in `try/catch`, and the exception becomes `success=0` plus an error text, or a message in `get_last_error`.

**32. Files can't be opened when the model path contains non-ASCII characters.** Upstream opens the model, voice prompt and reference audio with ANSI `fopen` and a narrow-character `std::filesystem::path`, but the path is passed in as UTF-8; a Japanese Windows user directory is exactly such a path, and `fopen` looks for a garbled file name interpreted as CP932. The read-only mapping in the patch initially just refused to load, and later it turned out the other tensors fail at the same place, so everything was changed to convert to wide characters first and use `_wfopen` / `CreateFileW` (`src/common/utf8_path.h`); tested with Japanese directory names.

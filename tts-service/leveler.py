"""Per-sentence loudness, peak limiting and pause shaping for a sentence-by-sentence TTS.

Usage: stage = leveler.begin(text); feed stage.process(chunk) for every PCM chunk
(float32, 24 kHz mono) and append stage.finish() at the end of the sentence.
``Leveler`` holds the settings and the time the last sentence ended; every ``begin``
returns a fresh ``Sentence`` with its own state, so sentences never share buffers.
"""
import time

import numpy as np

SAMPLE_RATE = 24000
SPEECH_FLOOR = 10 ** (-45 / 20)   # frames quieter than this count as silence
PRE_ROLL = int(0.06 * SAMPLE_RATE)
MEASURE = int(0.6 * SAMPLE_RATE)  # speech held back to set the sentence gain
SENTENCE_PAUSE = 0.9              # the server strips punctuation, so short
CLAUSE_PAUSE = 0.5                # interjections (うん、そっか) get the shorter pause
CLAUSE_CHARS = 6                  # ...which are told apart by length only (see --clause-chars)
LIMIT_CEILING = 10 ** (-6 / 20)   # headroom for Opus overshoot and the small speaker
SAFETY_CEILING = 10 ** (-3 / 20)
RELEASE = 1 - np.exp(-1 / 80)     # per 1 ms block: ~80 ms recovery after a peak
AMP_WAKE_SILENCE = 0.3            # before the first sentence after >1.5 s of quiet


class Leveler:
    """Even out one reply that is synthesized sentence by sentence.

    Each sentence is synthesized independently, so on the device consecutive
    phrases varied by 5-7 dB and the gaps between them by 0.2-1.25 s (the model's
    own 0.4-0.7 s lead-in silence plus an uneven tail). This trims the lead-in, gives
    each sentence one gain measured on its first 0.6 s of speech, limits peaks
    smoothly and ends every sentence with the same pause.

    The gain is a plain RMS over the first 0.6 s of voiced frames, not a loudness
    standard, and it is frozen for the rest of the sentence; measured on the
    benchmark replies it narrows the sentence-to-sentence spread from 4.6 to 3.7 dB.
    Adapting inside the sentence fought its natural stress and pumped, so the
    remaining spread is what the opening 0.6 s cannot predict.
    """

    def __init__(self, target_db=-18.0, max_boost_db=9.0, sentence_pause=SENTENCE_PAUSE,
                 clause_chars=CLAUSE_CHARS):
        self.sentence_pause = sentence_pause
        self.clause_pause = min(CLAUSE_PAUSE, sentence_pause)
        self.clause_chars = clause_chars
        self.target = 10 ** (target_db / 20)
        self.limit = 10 ** (max_boost_db / 20)
        self.last_end = 0.0

    def begin(self, text):
        return Sentence(self, text)

    def pause_for(self, text):
        """Short interjections get the clause pause; the server strips the punctuation
        that would tell a clause from a sentence, so length is the only cue. Tune
        ``clause_chars`` per language (6 suits Japanese and Chinese)."""
        return self.clause_pause if len(text.strip()) <= self.clause_chars else self.sentence_pause


class Sentence:
    """State of one sentence passing through the leveler."""

    def __init__(self, leveler, text):
        self.leveler = leveler
        self.text = text
        self.gain = 1.0
        self.env = 1.0
        self.block_gain = 1.0
        # The device powers its amplifier down when idle; the model's own lead-in
        # used to cover the power-up pop. Keep a short silence before the first
        # sentence of a reply, trim it for the following ones.
        idle = time.monotonic() - leveler.last_end
        self.lead = np.zeros(int(AMP_WAKE_SILENCE * SAMPLE_RATE) if idle > 1.5 else 0, np.float32)
        self.started = False
        self.held = []
        self.trailing_silence = 0

    @staticmethod
    def _frames_rms(wave, size=480):
        usable = len(wave) // size * size
        if not usable:
            return np.array([np.sqrt(np.mean(wave ** 2))]) if len(wave) else np.array([])
        return np.sqrt(np.mean(wave[:usable].reshape(-1, size) ** 2, axis=1))

    def _apply_gain(self, wave, onset=False):
        if onset:
            # One gain per sentence, measured on its first ~0.6 s: adapting inside a
            # sentence fought its natural stress and pumped; carrying a boost over
            # from a quiet sentence turned loud first syllables into pops.
            rms = self._frames_rms(wave)
            voiced = rms[rms > SPEECH_FLOOR]
            if len(voiced):
                limit = self.leveler.limit
                self.gain = float(np.clip(self.leveler.target / np.sqrt(np.mean(voiced ** 2)), 1 / limit, limit))
            self.env = 1.0
        return self._limit(wave * np.float32(self.gain))

    def _limit(self, out):
        """Smooth peak limiter. A tanh soft clip distorted audibly on a small device
        speaker, and Opus pushed its -0.4 dB peaks to full scale."""
        if not len(out):
            return out
        block = int(SAMPLE_RATE * 0.001)
        blocks = (len(out) + block - 1) // block
        padded = np.zeros(blocks * block, np.float32)
        padded[:len(out)] = out
        peaks = np.abs(padded.reshape(blocks, block)).max(axis=1)
        ahead = np.maximum(peaks, np.append(peaks[1:], peaks[-1:]))  # one block lookahead
        want = np.minimum(1.0, LIMIT_CEILING / np.maximum(ahead, 1e-9))
        gains = np.empty(blocks, np.float32)
        env = self.env
        # The release is a first-order recursion over 1 ms blocks: a few hundred
        # iterations per 0.32 s chunk, far below the synthesis time per chunk.
        for index, target in enumerate(want):
            env = target if target < env else env + (1.0 - env) * RELEASE
            gains[index] = env
        self.env = env
        starts = np.concatenate([[self.block_gain], gains[:-1]])
        ramp = (starts[:, None] + (gains - starts)[:, None] * np.linspace(0, 1, block, endpoint=False)[None, :])
        self.block_gain = float(gains[-1])
        limited = (padded * ramp.reshape(-1))[:len(out)]
        return np.clip(limited, -SAFETY_CEILING, SAFETY_CEILING)

    def process(self, wave):
        onset = False
        if not self.started:
            self.held.append(wave)
            joined = np.concatenate(self.held)
            loud = np.flatnonzero(np.abs(joined) > SPEECH_FLOOR * 4)
            if not len(loud) or len(joined) - loud[0] < MEASURE:
                return joined[:0]
            self.started = onset = True
            wave = joined[max(0, loud[0] - PRE_ROLL):]
        out = self._apply_gain(wave.astype(np.float32), onset)
        if onset:
            out = np.concatenate([self.lead, out])
        loud = np.flatnonzero(np.abs(out) > SPEECH_FLOOR * 4)
        self.trailing_silence = len(out) - 1 - loud[-1] if len(loud) else self.trailing_silence + len(out)
        return out

    def finish(self):
        self.leveler.last_end = time.monotonic()
        pause = np.zeros(int(self.leveler.pause_for(self.text) * SAMPLE_RATE), np.float32)
        if not self.started:
            if not self.held:
                return np.zeros(0, np.float32)
            joined = np.concatenate(self.held)
            loud = np.flatnonzero(np.abs(joined) > SPEECH_FLOOR * 4)
            if not len(loud):  # never crossed the floor: pass the audio through untouched
                return joined
            # a sentence shorter than the measuring window
            self.started = True
            out = self._apply_gain(joined[max(0, loud[0] - PRE_ROLL):].astype(np.float32), onset=True)
            loud = np.flatnonzero(np.abs(out) > SPEECH_FLOOR * 4)
            self.trailing_silence = len(out) - 1 - loud[-1] if len(loud) else 0
            return np.concatenate([self.lead, out, pause[:max(0, len(pause) - self.trailing_silence)]])
        return pause[:max(0, len(pause) - self.trailing_silence)]

"""Slow automatic gain control on the outgoing PCM.

tts-service's leveler sets one gain per sentence from its first 0.6 s; what the rest of the
sentence does is up to the model, and on a small speaker a 6 dB sag mid-sentence is audible.
This runs frame by frame with no lookahead: measure the frame's RMS and move the gain toward
the target, cutting fast (``attack_db_s``) and boosting slowly (``release_db_s``) so a loud
opening is caught at once while quiet stretches come up without pumping; the gain holds over
pauses and stays within ``max_boost_db`` / ``max_cut_db``. A soft limiter keeps peaks under
``ceiling_db``. Measured with tools/agc_check.py (three fixed replies, 0.25 s windows above -40 dBFS):
the p90-p10 loudness spread goes from 5.8-7.5 dB to 4.7-6.0 dB. A 300 ms lookahead did not help.
"""
import math

import numpy as np


class Agc:
    def __init__(self, sample_rate=24000, target_db=-20.0, attack_db_s=60.0, release_db_s=6.0, max_boost_db=8.0,
                 max_cut_db=10.0, floor_db=-45.0, ceiling_db=-3.0, frame_ms=60):
        self.target = 10 ** (target_db / 20)
        self.attack = attack_db_s * frame_ms / 1000     # dB per frame when cutting
        self.release = release_db_s * frame_ms / 1000   # dB per frame when boosting
        self.max_boost, self.max_cut = max_boost_db, -max_cut_db
        self.floor = 10 ** (floor_db / 20)
        self.ceiling = 10 ** (ceiling_db / 20)
        self.gain_db = 0.0

    def reset(self):
        self.gain_db = 0.0

    def process(self, frame: np.ndarray) -> np.ndarray:
        """``frame`` is int16; returns int16 of the same length."""
        x = frame.astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(x * x))) if len(x) else 0.0
        if rms > self.floor:                            # speech: nudge the gain toward the target
            wanted = 20 * math.log10(self.target / rms)
            wanted = min(self.max_boost, max(self.max_cut, wanted))
            delta = wanted - self.gain_db
            step = self.attack if delta < 0 else self.release
            self.gain_db += max(-step, min(step, delta))
        y = x * 10 ** (self.gain_db / 20)
        peak = float(np.abs(y).max()) if len(y) else 0.0
        if peak > self.ceiling:                         # soft knee: scale the whole frame, no clipping
            y *= self.ceiling / peak
        return (np.clip(y, -1, 1) * 32767).astype(np.int16)

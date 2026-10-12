"""tail_gate: a quiet, non-periodic tail after the last voiced audio is trimmed."""
import numpy as np
import pytest

from tail_gate import TailGate

RATE = 24000


def tone(seconds, amplitude, hz=180.0):
    t = np.arange(int(seconds * RATE)) / RATE
    return (amplitude * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def run(wave, chunk=int(0.32 * RATE)):
    gate = TailGate()
    out = [gate.feed(wave[i:i + chunk]) for i in range(0, len(wave), chunk)]
    out.append(gate.finish())
    return gate, np.concatenate(out)


def test_breath_after_the_last_word_is_trimmed():
    rng = np.random.default_rng(1)
    breath = (rng.standard_normal(int(0.6 * RATE)) * 0.0025).astype(np.float32)
    wave = np.concatenate([tone(1.0, 0.3), np.zeros(int(0.2 * RATE), np.float32), breath])
    gate, out = run(wave)
    assert gate.trimmed_samples > 0
    assert len(out) < len(wave)
    assert len(out) >= int(1.0 * RATE)  # the voiced part is intact


def test_voiced_tail_is_kept():
    wave = tone(1.5, 0.3)
    gate, out = run(wave)
    assert gate.trimmed_samples == 0
    assert len(out) == len(wave)


def test_gate_cannot_be_used_after_finish():
    gate = TailGate()
    gate.feed(tone(0.5, 0.2))
    gate.finish()
    with pytest.raises(RuntimeError):
        gate.feed(tone(0.1, 0.2))

"""leveler: one gain per sentence, trimmed lead-in, limited peaks, uniform pause."""
import numpy as np

from leveler import AMP_WAKE_SILENCE, CLAUSE_PAUSE, Leveler, SAFETY_CEILING, SAMPLE_RATE, Sentence

CHUNK = int(0.32 * SAMPLE_RATE)


def tone(seconds, amplitude, hz=220.0):
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    return (amplitude * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def silence(seconds):
    return np.zeros(int(seconds * SAMPLE_RATE), np.float32)


def run(leveler, text, wave):
    stage = leveler.begin(text)
    out = [stage.process(wave[i:i + CHUNK]) for i in range(0, len(wave), CHUNK)]
    out.append(stage.finish())
    return np.concatenate(out)


def db(x):
    return 20 * np.log10(np.sqrt(np.mean(np.square(x))) + 1e-12)


def voiced(out):
    loud = np.flatnonzero(np.abs(out) > 1e-3)
    return out[loud[0]:loud[-1] + 1]


def test_quiet_and_loud_sentences_land_on_the_same_level():
    leveler = Leveler(sentence_pause=0.9)
    # -25 dBFS and -11 dBFS sentences, both within the ±9 dB gain range, land on the -18 dBFS target
    quiet = run(leveler, "こちらは静かな文です。", np.concatenate([silence(0.5), tone(1.5, 0.08), silence(0.2)]))
    loud = run(leveler, "こちらは大きな文です。", np.concatenate([silence(0.6), tone(1.5, 0.4), silence(0.3)]))
    assert abs(db(voiced(quiet)) - db(voiced(loud))) < 1.0
    assert abs(db(voiced(loud)) + 18.0) < 1.5
    assert np.abs(loud).max() <= SAFETY_CEILING + 1e-6


def test_peaks_are_limited_below_the_safety_ceiling():
    out = run(Leveler(), "とても大きな声の文です。", np.concatenate([silence(0.3), tone(1.2, 0.99), silence(0.2)]))
    assert np.abs(out).max() <= SAFETY_CEILING + 1e-6


def test_lead_in_is_trimmed_and_pause_is_uniform():
    leveler = Leveler(sentence_pause=0.9)
    first = run(leveler, "これは最初の文です。", np.concatenate([silence(0.7), tone(1.0, 0.2), silence(0.05)]))
    second = run(leveler, "これは次の文です。", np.concatenate([silence(0.4), tone(1.0, 0.2), silence(0.6)]))
    # first sentence after idle keeps the amplifier wake-up silence, later ones only the pre-roll
    onset_first = np.flatnonzero(np.abs(first) > 1e-3)[0] / SAMPLE_RATE
    onset_second = np.flatnonzero(np.abs(second) > 1e-3)[0] / SAMPLE_RATE
    assert AMP_WAKE_SILENCE <= onset_first < AMP_WAKE_SILENCE + 0.1
    assert onset_second < 0.1
    for out in (first, second):
        tail = (len(out) - 1 - np.flatnonzero(np.abs(out) > 1e-3)[-1]) / SAMPLE_RATE
        assert abs(tail - 0.9) < 0.03


def test_short_interjection_is_leveled_in_finish_with_the_clause_pause():
    leveler = Leveler(sentence_pause=0.9, clause_chars=6)
    out = run(leveler, "うん", np.concatenate([silence(0.3), tone(0.25, 0.08), silence(0.1)]))
    assert abs(db(voiced(out)) + 18.0) < 1.5
    tail = (len(out) - 1 - np.flatnonzero(np.abs(out) > 1e-3)[-1]) / SAMPLE_RATE
    assert abs(tail - CLAUSE_PAUSE) < 0.03


def test_silence_passes_through_untouched():
    leveler = Leveler()
    wave = silence(0.8)
    out = run(leveler, "……", wave)
    assert len(out) == len(wave) and not out.any()


def test_each_sentence_gets_its_own_state():
    leveler = Leveler()
    a, b = leveler.begin("a"), leveler.begin("b")
    assert isinstance(a, Sentence) and a is not b
    a.process(tone(1.0, 0.3))
    assert b.started is False and b.held == []

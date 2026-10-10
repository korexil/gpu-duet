"""Server-side wake word gate for an always-open xiaozhi listening session.

ESP32 boards detect the wake word on-device with ESP-SR. For names that ESP-SR
has no model for (e.g. a Japanese name), the usual workaround is a MultiNet
command made of Chinese pinyin or English phonemes that only approximates the
name. Measured on one board with 60 natural TTS utterances of a 3-mora Japanese
name, that woke 3/60 (tuned: at best 29/60), while SenseVoice on the same clips
produced the name (or a homophone spelling) 53/60 times and rejected all 20
near-miss words.

With the gate enabled the board keeps one listening session open (start it once
with the button or the on-board wake word). Every utterance is recognized on the
server, and the assistant only answers after hearing its name, then for
`window_seconds` after each of its replies without the name. When the window
runs out it says the configured end_prompt goodbye once and goes back to
waiting for its name; the board stays connected.

Settings live in data/wake_gate.json and are re-read when the file changes:

    {
      "enabled": true,
      "window_seconds": 120,
      "farewell": true,
      "wake_patterns": ["name", "a homophone", "a frequent mis-recognition"]
    }

The config's existing ``wakeup_words`` are always accepted as well.
"""
import json
import os
import re
import time

GATE_FILE = "data/wake_gate.json"
DEFAULTS = {"enabled": False, "window_seconds": 60, "farewell": True, "wake_patterns": []}
FILLER = re.compile(r"^[\s、。，,.!！?？ー〜~ねえあのおいちょっと]*$")
PUNCT = re.compile(r"^[\s、。，,.!！?？]+|[\s、。，,.!！?？]+$")

_cache = {"mtime": None, "value": dict(DEFAULTS)}


def settings():
    try:
        mtime = os.path.getmtime(GATE_FILE)
    except OSError:
        return dict(DEFAULTS)
    if mtime != _cache["mtime"]:
        try:
            with open(GATE_FILE, encoding="utf-8") as handle:
                _cache["value"] = {**DEFAULTS, **json.load(handle)}
        except (OSError, ValueError):
            _cache["value"] = dict(DEFAULTS)
        _cache["mtime"] = mtime
    return _cache["value"]


def enabled():
    return bool(settings().get("enabled"))


def _wake_pattern(conn):
    words = [re.escape(w) for w in (conn.config.get("wakeup_words") or []) if w]
    words += [p for p in settings().get("wake_patterns") or [] if p]
    return re.compile("(" + "|".join(words) + ")", re.IGNORECASE) if words else None


def mark_awake(conn):
    conn.wake_until = time.time() + float(settings().get("window_seconds", 60))
    conn.farewell_pending = True


def extend_after_speech(conn):
    """Count the follow-up window from the end of the reply, not from the question."""
    if enabled() and getattr(conn, "wake_until", 0) > time.time() - 1:
        mark_awake(conn)


def due_for_farewell(conn):
    if not enabled() or not settings().get("farewell", True):
        return False
    if not getattr(conn, "farewell_pending", False) or getattr(conn, "client_is_speaking", False):
        return False
    return time.time() > getattr(conn, "wake_until", 0)


def begin_farewell(conn):
    conn.farewell_pending = False
    conn.wake_until = 0
    conn.gate_bypass_once = True  # the farewell prompt itself must pass the gate


def filter_utterance(conn, text):
    """Return the text to answer, or None to stay silent.

    "<name>, how cold it is" -> "how cold it is"; the name alone is passed through
    unchanged so the LLM can answer being called.
    """
    if not enabled():
        return text
    if getattr(conn, "gate_bypass_once", False):
        conn.gate_bypass_once = False
        return text
    pattern = _wake_pattern(conn)
    match = pattern.search(text) if pattern else None
    if match:
        mark_awake(conn)
        before, after = text[:match.start()], text[match.end():]
        rest = PUNCT.sub("", before if FILLER.match(after) else after)
        return text if not rest or FILLER.match(rest) else rest
    if getattr(conn, "wake_until", 0) > time.time() or getattr(conn, "client_is_speaking", False):
        mark_awake(conn)
        return text
    return None

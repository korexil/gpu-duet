"""wakeGate: server-side name gating for an always-open session."""
import json
import time
from types import SimpleNamespace

import pytest

from conftest import load_module

wakeGate = load_module("wakeGate", "server/core/handle/wakeGate.py")


@pytest.fixture
def gate(tmp_path, monkeypatch):
    path = tmp_path / "wake_gate.json"

    def write(**values):
        path.write_text(json.dumps({"enabled": True, "window_seconds": 60, **values}), encoding="utf-8")
        wakeGate._cache["mtime"] = None

    monkeypatch.setattr(wakeGate, "GATE_FILE", str(path))
    write(wake_patterns=["ミナト", "湊"])
    return write


def conn():
    return SimpleNamespace(config={"wakeup_words": ["你好小智"]}, client_is_speaking=False)


def test_disabled_gate_passes_everything(gate):
    gate(enabled=False)
    assert wakeGate.filter_utterance(conn(), "今日は寒いね") == "今日は寒いね"


def test_unaddressed_speech_is_ignored(gate):
    assert wakeGate.filter_utterance(conn(), "今日は寒いね") is None


def test_name_with_request_wakes_and_strips_name(gate):
    c = conn()
    assert wakeGate.filter_utterance(c, "ミナト、今日は寒いね") == "今日は寒いね"
    assert wakeGate.filter_utterance(c, "今日は寒いね、湊") == "今日は寒いね"
    assert wakeGate.filter_utterance(c, "それでどうしたの？") == "それでどうしたの？"


def test_name_alone_is_passed_through(gate):
    assert wakeGate.filter_utterance(conn(), "ねえ、ミナト") == "ねえ、ミナト"


def test_config_wakeup_words_also_wake(gate):
    assert wakeGate.filter_utterance(conn(), "你好小智，今天天气怎么样") == "今天天气怎么样"


def test_window_expires_then_farewell_once_then_sleep(gate):
    gate(wake_patterns=["ミナト"], window_seconds=0.05)
    c = conn()
    wakeGate.filter_utterance(c, "ミナト")
    assert not wakeGate.due_for_farewell(c)
    time.sleep(0.08)
    assert wakeGate.due_for_farewell(c)
    wakeGate.begin_farewell(c)
    assert wakeGate.filter_utterance(c, "end prompt text") == "end prompt text"
    assert wakeGate.filter_utterance(c, "今日は寒いね") is None
    assert not wakeGate.due_for_farewell(c)


def test_no_farewell_if_never_woken(gate):
    assert not wakeGate.due_for_farewell(conn())


def test_window_counts_from_end_of_reply(gate):
    gate(wake_patterns=["ミナト"], window_seconds=0.05)
    c = conn()
    wakeGate.filter_utterance(c, "ミナト")
    time.sleep(0.03)
    wakeGate.extend_after_speech(c)
    time.sleep(0.03)
    assert wakeGate.filter_utterance(c, "まだ聞いてる？") == "まだ聞いてる？"

"""llamacpp_paced: token-exact chunked continuation that yields the GPU to the TTS.

Loaded without xiaozhi through tools/turn_taking_sim.load_provider.
"""
import json
import time

import httpx

from turn_taking_sim import load_provider

TOKENS = {1: "今日は", 2: "寒い", 3: "ね。", 4: "あったかい", 5: "お茶", 6: "が", 7: "いいな。"}
EOS = 99


def fake_llama(reply_ids, seen):
    def handler(request):
        body = json.loads(request.content or b"{}")
        if request.url.path == "/apply-template":
            seen["messages"] = body["messages"]
            return httpx.Response(200, json={"prompt": "<rendered>"})
        if request.url.path == "/completion":
            rendered, *ids = body["prompt"]
            seen.setdefault("prompts", []).append(list(ids))
            remaining = reply_ids[len(ids):]
            chunk = remaining[:body["n_predict"]]
            stop = "eos" if chunk and chunk[-1] == EOS else "limit"
            return httpx.Response(200, json={"tokens": chunk, "stop_type": stop})
        if request.url.path == "/detokenize":
            return httpx.Response(200, json={"content": "".join(TOKENS.get(t, "<eos>") for t in body["tokens"])})
        raise AssertionError(request.url)
    return handler


def provider(reply_ids, seen, health_states=None, **config):
    """health_states: list of /health bodies returned in order; the last one repeats."""
    p = load_provider()({"base_url": "http://llm/v1", "chunk_tokens": 2, **config})
    p.client = httpx.Client(base_url="http://llm", transport=httpx.MockTransport(fake_llama(reply_ids, seen)))
    states = list(health_states or [{"busy": False}])

    def health(request):
        seen["health"] = seen.get("health", 0) + 1
        return httpx.Response(200, json=states.pop(0) if len(states) > 1 else states[0])
    p.voice = httpx.Client(transport=httpx.MockTransport(health))
    return p


def test_continuation_is_token_exact_and_drops_end_token():
    seen = {}
    out = "".join(provider([1, 2, 3, 4, 5, 6, 7, EOS], seen).response("s", [{"role": "user", "content": "寒いね"}]))
    assert out == "今日は寒いね。あったかいお茶がいいな。"
    assert seen["prompts"] == [[], [1, 2], [1, 2, 3, 4], [1, 2, 3, 4, 5, 6]]


def test_waits_for_tts_after_each_sentence():
    seen = {}
    p = provider([1, 2, 3, 4, 5, 6, 7, EOS], seen, [{"busy": True}, {"busy": True}, {"busy": False}],
                 voice_health_url="http://tts/health")
    assert "".join(p.response("s", [{"role": "user", "content": "x"}])).endswith("いいな。")
    assert seen["health"] >= 3


def test_pickup_is_seen_through_the_started_counter():
    """A sentence that finishes between two polls never shows busy; the counter still proves the hand-off."""
    seen = {}
    p = provider([1, 2, 3, 4, 5, 6, 7, EOS], seen, [{"busy": False, "started": 4}, {"busy": False, "started": 5}],
                 voice_health_url="http://tts/health", handoff_wait=2.0)
    t0 = time.monotonic()
    list(p.response("s", [{"role": "user", "content": "x"}]))
    assert time.monotonic() - t0 < 1.0   # did not sit out the full handoff_wait


def test_handoff_wait_bounds_the_stall_when_the_splitter_holds_the_sentence():
    seen = {}
    p = provider([1, 2, 3, 4, 5, 6, 7, EOS], seen, [{"busy": False, "started": 1}],
                 voice_health_url="http://tts/health", handoff_wait=0.15)
    t0 = time.monotonic()
    out = "".join(p.response("s", [{"role": "user", "content": "x"}]))
    elapsed = time.monotonic() - t0
    assert out.endswith("いいな。")
    assert 0.15 <= elapsed < 0.8   # one bounded wait after the first sentence, answer still complete


def test_probe_failure_never_blocks_the_answer():
    seen = {}
    p = provider([1, 2, 3, EOS], seen, voice_health_url="http://tts/health", handoff_wait=0.1)
    p.voice = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert "".join(p.response("s", [{"role": "user", "content": "x"}])) == "今日は寒いね。"


def test_question_rest_note_only_after_a_question():
    seen = {}
    note = "（今回は質問で終わらせない）"
    dialogue = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "元気？"},
                {"role": "user", "content": "うん"}]
    list(provider([1, EOS], seen, question_rest_note=note).response("s", dialogue))
    assert seen["messages"][-1]["content"] == "うん" + note
    assert dialogue[-1]["content"] == "うん"  # caller's history untouched

    dialogue[1]["content"] = "元気だよ。"
    list(provider([1, EOS], seen, question_rest_note=note).response("s", dialogue))
    assert seen["messages"][-1]["content"] == "うん"


def test_functions_are_ignored_and_the_stream_is_plain_text():
    seen = {}
    pairs = list(provider([1, 2, EOS], seen).response_with_functions("s", [{"role": "user", "content": "x"}],
                                                                     functions=[{"name": "set_volume"}]))
    assert "".join(text for text, _ in pairs) == "今日は寒い"
    assert all(call is None for _, call in pairs)

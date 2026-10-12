"""llama.cpp server provider that takes turns with a local TTS on the same GPU.

A streaming TTS decodes frame by frame with many small synchronous kernels. Any
concurrent LLM decoding on the same GPU stretches every step: on an 8 GB laptop
GPU the first sentence ran at RTF 1.1-1.6 while the LLM was still generating,
leaving audible gaps. Rate-limiting the LLM did not help (RTF stayed ~1.4), but
together the two only need ~75% of the GPU, so this provider serializes them:
it generates up to a sentence end, waits while the TTS reports ``busy``, and
continues in the TTS's idle gaps. Measured in bench/ (6 runs per cell, median,
~80 s answers): first audio 3.20 s -> 1.83 s, playback underrun 2.27 s -> 0.

Continuation is token-exact: ``prompt = [rendered chat template, *generated ids]``
keeps the KV-cache prefix identical and avoids re-tokenization drift.

Hand-off: after yielding a sentence the provider polls ``voice_health_url`` until
the TTS reports ``busy`` or its ``started`` counter advances (a sentence shorter
than one poll would otherwise go unseen), then waits until ``busy`` clears. The
wait for the pick-up is bounded by ``handoff_wait``: the pipeline's sentence
splitter may merge or hold a fragment, and that must not stall the answer. If the
splitter regularly takes longer than ``handoff_wait`` to forward a sentence, the
two overlap on the GPU again; raise the value or shorten the splitter's buffer.

Tool calling is not implemented: ``response_with_functions`` streams a plain
answer and ignores ``functions`` (logged once). Route tools through another path.

Config (ai_model_config / config.yaml), all optional except base_url:
    type: llamacpp_paced
    base_url: http://host.docker.internal:18080
    voice_health_url: http://host.docker.internal:18766/health   # omit to disable turn-taking
    max_tokens: 400
    temperature: 0.8, top_p: 0.95, top_k: 64
    chunk_tokens: 16        # tokens per /completion call
    turn_tokens: 48         # yield to the TTS at least this often
    handoff_wait: 0.4       # seconds to wait for the TTS to pick a sentence up
    question_rest_note: ""  # appended to the user turn after a reply that ended in a question
"""
import time

import httpx

from config.logger import setup_logging
from core.providers.llm.base import LLMProviderBase

TAG = __name__
logger = setup_logging()

SENTENCE_END = "。！？!?\n"
POLL = 0.02
SENTENCE_TIMEOUT = 30


class LLMProvider(LLMProviderBase):
    def __init__(self, config):
        self.base_url = config.get("base_url", "http://host.docker.internal:18080").rstrip("/")
        if self.base_url.endswith("/v1"):
            self.base_url = self.base_url[:-3]
        self.max_tokens = int(config.get("max_tokens") or 400)
        self.sampling = {
            "temperature": float(config.get("temperature", 0.8)),
            "top_p": float(config.get("top_p", 0.95)),
            "top_k": int(config.get("top_k", 64)),
        }
        self.chunk_tokens = int(config.get("chunk_tokens", 16))
        self.turn_tokens = int(config.get("turn_tokens", 48))
        self.handoff_wait = float(config.get("handoff_wait", 0.4))
        self.voice_health_url = config.get("voice_health_url")
        self.question_rest_note = config.get("question_rest_note") or ""
        self.client = httpx.Client(base_url=self.base_url, timeout=httpx.Timeout(60, connect=3))
        self.voice = httpx.Client(timeout=httpx.Timeout(1, connect=0.5))
        self._functions_warned = False

    def _post(self, path, body):
        response = self.client.post(path, json=body)
        response.raise_for_status()
        return response.json()

    def _voice_state(self):
        """(busy, started) from the TTS; a failed probe reads as idle so it never blocks the answer."""
        try:
            data = self.voice.get(self.voice_health_url).json()
            return bool(data.get("busy")), int(data.get("started") or 0)
        except Exception:
            return False, 0

    def _yield_gpu_to_voice(self):
        """After handing over a sentence, wait for the TTS to pick it up and finish it."""
        busy, started_before = self._voice_state()
        deadline = time.monotonic() + self.handoff_wait
        while not busy:
            if time.monotonic() > deadline:
                return
            time.sleep(POLL)
            busy, started = self._voice_state()
            if started > started_before:
                break  # picked up, possibly already finished
        deadline = time.monotonic() + SENTENCE_TIMEOUT
        while busy and time.monotonic() < deadline:
            time.sleep(POLL)
            busy, _ = self._voice_state()

    def _with_turn_hint(self, dialogue):
        """Optional: after a reply that ended in a question, append question_rest_note.

        The note goes on the last user message, not the system prompt, so the
        cached prefix of the conversation stays reusable.
        """
        if not self.question_rest_note:
            return dialogue
        last_reply = next((m.get("content") or "" for m in reversed(dialogue)
                           if m.get("role") == "assistant" and m.get("content")), "")
        if not last_reply.rstrip("」』）) ").endswith(("？", "?")):
            return dialogue
        for index in range(len(dialogue) - 1, -1, -1):
            message = dialogue[index]
            if message.get("role") == "user" and isinstance(message.get("content"), str):
                hinted = dict(message, content=message["content"] + self.question_rest_note)
                return dialogue[:index] + [hinted] + dialogue[index + 1:]
        return dialogue

    def response(self, session_id, dialogue, **kwargs):
        dialogue = self._with_turn_hint(dialogue)
        rendered = self._post("/apply-template", {"messages": dialogue})["prompt"]
        max_tokens = int(kwargs.get("max_tokens") or self.max_tokens)
        ids, text, emitted = [], "", ""
        turn_start = 0
        while len(ids) < max_tokens:
            result = self._post("/completion", {
                "prompt": [rendered, *ids], "n_predict": min(self.chunk_tokens, max_tokens - len(ids)),
                "cache_prompt": True, "return_tokens": True, **self.sampling,
            })
            tokens = result.get("tokens") or []
            if result.get("stop_type") == "eos" and tokens:
                tokens = tokens[:-1]  # the end-of-turn token would detokenize as text
            ids += tokens
            text = self._post("/detokenize", {"tokens": ids})["content"] if ids else ""
            # Hold back an incomplete multi-byte character until the next chunk completes it.
            stable = text.rstrip("�")
            new_text = stable[len(emitted):]
            if new_text:
                yield new_text
                emitted = stable
            if result.get("stop_type") != "limit" or not result.get("tokens"):
                break
            if self.voice_health_url and (any(c in new_text for c in SENTENCE_END)
                                          or len(ids) - turn_start >= self.turn_tokens):
                self._yield_gpu_to_voice()
                turn_start = len(ids)
        if len(text) > len(emitted):
            yield text[len(emitted):]

    def response_with_functions(self, session_id, dialogue, functions=None, **kwargs):
        if functions and not self._functions_warned:
            self._functions_warned = True
            logger.bind(tag=TAG).warning("llamacpp_paced does not support tool calling; ignoring functions")
        for token in self.response(session_id, dialogue, **kwargs):
            yield token, None

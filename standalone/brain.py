"""The LLM side: the paced llama-server provider from server/, loaded without xiaozhi, plus a
sentence splitter that hands the TTS its first sentence early."""
import importlib.util
import sys
import types
from pathlib import Path

PROVIDER = Path(__file__).resolve().parents[1] / 'server/core/providers/llm/llamacpp_paced/llamacpp_paced.py'
SENTENCE_END = '。！？!?\n'
CLAUSE_END = '，、,;；：:'
QUOTE_OPEN, QUOTE_CLOSE = '「『“(（[【', '」』”)）]】'


def load_provider():
    """llamacpp_paced imports xiaozhi's provider base class and logger; stub both and load it by path."""
    for name in ('config', 'config.logger', 'core', 'core.providers', 'core.providers.llm', 'core.providers.llm.base'):
        sys.modules.setdefault(name, types.ModuleType(name))

    class _Log:
        def bind(self, **_):
            return self

        def __getattr__(self, _):
            return lambda *a, **k: None

    sys.modules['config.logger'].setup_logging = lambda: _Log()
    sys.modules['core.providers.llm.base'].LLMProviderBase = object
    spec = importlib.util.spec_from_file_location('llamacpp_paced', PROVIDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.LLMProvider


class SentenceSplitter:
    """Feed streamed text, get chunks for the TTS back.

    Every chunk is a separate synthesis, and the model cannot carry intonation across them, so
    chunks should be few; but a 1.7B TTS drifts (flat, word-by-word reading) on long inputs, so
    they should not be long either. A sentence end only counts once ``min_chars`` are buffered
    (one or two sentences per chunk by default), a clause mark counts after ``max_chars``, and
    ``first_cut_chars`` > 0 lets the first chunk out at a clause mark that early, trading
    natural phrasing for a faster first sound. Nothing is cut inside an open quote or bracket.
    """

    def __init__(self, first_cut_chars=0, max_chars=70, min_chars=30, first_min_chars=None):
        self.first_cut_chars, self.max_chars, self.min_chars = first_cut_chars, max_chars, min_chars
        # the first chunk may leave at the first sentence end past this, so the first sound comes sooner
        self.first_min_chars = min_chars if first_min_chars is None else first_min_chars
        self.buffer, self.count = '', 0

    def feed(self, text):
        self.buffer += text
        out = []
        while (cut := self._cut()) is not None:
            sentence, self.buffer = self.buffer[:cut + 1].strip(), self.buffer[cut + 1:]
            if sentence.strip(''.join(SENTENCE_END + CLAUSE_END)):
                out.append(sentence)
                self.count += 1
        return out

    def flush(self):
        rest, self.buffer = self.buffer.strip(), ''
        if rest.strip(''.join(SENTENCE_END + CLAUSE_END)):
            self.count += 1
            return rest
        return None

    def _cut(self):
        depth = 0
        for i, ch in enumerate(self.buffer):
            if ch in QUOTE_OPEN:
                depth += 1
            elif ch in QUOTE_CLOSE:
                depth = max(0, depth - 1)
            if depth:
                continue
            need = self.first_min_chars if self.count == 0 else self.min_chars
            if ch in SENTENCE_END and (i + 1 >= need or ch == '\n'):
                return i
            limit = self.max_chars
            if self.count == 0 and self.first_cut_chars:
                limit = min(limit, self.first_cut_chars)
            if ch in CLAUSE_END and i + 1 >= limit:
                return i
        return None


class Brain:
    """One answer at a time: ``reply(dialogue)`` streams text pieces from the paced provider."""

    def __init__(self, cfg, tts_health_url=None):
        config = {
            'base_url': cfg.get('base_url', 'http://127.0.0.1:18080'),
            'max_tokens': cfg.get('max_tokens', 400),
            'temperature': cfg.get('temperature', 0.8),
            'top_p': cfg.get('top_p', 0.95),
            'top_k': cfg.get('top_k', 64),
            'chunk_tokens': cfg.get('chunk_tokens', 16),
            'turn_tokens': cfg.get('turn_tokens', 48),
            'handoff_wait': cfg.get('handoff_wait', 0.4),
            'question_rest_note': cfg.get('question_rest_note', ''),
        }
        if tts_health_url and cfg.get('turn_taking', True):
            config['voice_health_url'] = tts_health_url
        self.provider = load_provider()(config)
        self.base_url = config['base_url']

    def reply(self, dialogue):
        return self.provider.response('standalone', dialogue)

    def health(self):
        try:
            return self.provider.client.get('/health', timeout=1.0).status_code == 200
        except Exception:
            return False


class Dialogue:
    """System prompt plus the last ``turns`` exchanges."""

    def __init__(self, system_prompt, turns=10):
        self.system_prompt, self.turns = system_prompt, turns
        self.messages = []

    def user(self, text):
        self.messages.append({'role': 'user', 'content': text})
        self._trim()

    def assistant(self, text):
        if text.strip():
            self.messages.append({'role': 'assistant', 'content': text.strip()})
        self._trim()

    def _trim(self):
        self.messages = self.messages[-2 * self.turns:]

    def render(self):
        system = [{'role': 'system', 'content': self.system_prompt}] if self.system_prompt else []
        return system + list(self.messages)

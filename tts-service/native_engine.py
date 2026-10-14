"""ctypes binding for the C ABI of the patched qwen3-tts.cpp bridge (qwen3_tts_bridge.dll/.so).

Streams 24 kHz float PCM through a callback while synthesizing with a pre-extracted
in-context-learning (voice clone) prompt. See native-tts/README.md for the build.
"""
import ctypes as C
import os
import random
import sys
import time

import numpy as np

from tail_gate import TailGate


class Params(C.Structure):
    _fields_ = [('max_audio_tokens', C.c_int32), ('temperature', C.c_float), ('top_p', C.c_float),
                ('top_k', C.c_int32), ('n_threads', C.c_int32), ('print_progress', C.c_int32),
                ('print_timing', C.c_int32), ('repetition_penalty', C.c_float), ('language_id', C.c_int32),
                ('instruction', C.c_char_p), ('speaker', C.c_char_p), ('vocoder_left_context_sec', C.c_float),
                ('seed', C.c_int64), ('full_text_input', C.c_int32)]


class Streaming(C.Structure):
    _fields_ = [('generation', Params), ('chunk_sec', C.c_float), ('left_context_sec', C.c_float),
                ('collect_audio', C.c_int32)]


class Chunk(C.Structure):
    _fields_ = [('samples', C.POINTER(C.c_float)), ('n_samples', C.c_int32), ('sample_rate', C.c_int32),
                ('start_sample', C.c_int64), ('end_sample', C.c_int64), ('start_frame', C.c_int32),
                ('end_frame', C.c_int32), ('start_text_byte', C.c_int32), ('end_text_byte', C.c_int32),
                ('text_alignment_kind', C.c_int32), ('confidence', C.c_float)]


class Result(C.Structure):
    _fields_ = [('audio', C.POINTER(C.c_float)), ('audio_len', C.c_int32), ('sample_rate', C.c_int32),
                ('success', C.c_int32), ('error_msg', C.c_char_p), ('t_total_ms', C.c_int64)]


ABI_STRUCTS = (Params, Streaming, Chunk, Result)
Callback = C.CFUNCTYPE(C.c_int32, C.POINTER(Chunk), C.c_void_p)

# talker_config.codec_language_id of Qwen3-TTS-12Hz-1.7B-Base
LANGUAGE_IDS = {'zh': 2055, 'en': 2050, 'de': 2053, 'it': 2070, 'pt': 2071, 'es': 2054,
                'ja': 2058, 'ko': 2064, 'fr': 2061, 'ru': 2069}

CODEC_HZ = 12            # audio tokens per second of speech
MIN_TOKENS = 128         # ~10.7 s: enough for any short reply even at a slow pace
MAX_TOKENS = 1024        # ~85 s: the hard stop for a runaway generation
TOKENS_PER_CHAR = 8      # ~0.67 s per character, 2-3x the slowest CJK speech rate


def expected_layout():
    """Mirror of qwen3_tts_bridge_abi_layout: each struct's size, then every field offset."""
    layout = []
    for struct in ABI_STRUCTS:
        layout.append(C.sizeof(struct))
        layout += [getattr(struct, name).offset for name, _ in struct._fields_]
    return layout


TYPE_CODES = {C.c_int32: 1, C.c_int64: 2, C.c_float: 3}


def expected_types():
    """Mirror of qwen3_tts_bridge_abi_types: one code per field (1 int32, 2 int64, 3 float,
    4 pointer, 5 nested struct). Catches a field retyped to another type of the same width,
    which leaves the layout unchanged."""
    codes = []
    for struct in ABI_STRUCTS:
        for _, kind in struct._fields_:
            codes.append(TYPE_CODES.get(kind, 4 if kind is C.c_char_p or isinstance(kind, type) and issubclass(kind, C._Pointer)
                                        else 5))
    return codes


def _read_table(lib, name, expected):
    function = getattr(lib, name)
    function.argtypes = [C.POINTER(C.c_int32), C.c_int32]
    function.restype = C.c_int32
    values = (C.c_int32 * len(expected))()
    count = function(values, len(expected))
    actual = list(values)[:min(count, len(expected))]
    if count != len(expected) or actual != expected:
        raise RuntimeError(f'ABI {name} mismatch: library {actual} ({count} values) vs binding {expected}')


def check_abi(lib):
    """Refuse a library whose structs differ from this binding: field offsets and sizes,
    then field types. Older libraries expose less (layout only, or only the sizes);
    those still load, with a warning saying what stayed unverified."""
    if hasattr(lib, 'qwen3_tts_bridge_abi_layout'):
        _read_table(lib, 'qwen3_tts_bridge_abi_layout', expected_layout())
        if hasattr(lib, 'qwen3_tts_bridge_abi_types'):
            _read_table(lib, 'qwen3_tts_bridge_abi_types', expected_types())
            return 'layout+types'
        print('warning: library predates qwen3_tts_bridge_abi_types; field offsets match but a '
              'same-width type change would go unnoticed', file=sys.stderr)
        return 'layout'
    lib.qwen3_tts_bridge_abi_sizes.argtypes = [C.POINTER(C.c_int32)]
    sizes = (C.c_int32 * 4)()
    lib.qwen3_tts_bridge_abi_sizes(sizes)
    expected_sizes = [C.sizeof(t) for t in ABI_STRUCTS]
    if list(sizes) != expected_sizes:
        raise RuntimeError(f'ABI mismatch: library {list(sizes)} vs binding {expected_sizes}')
    print('warning: library predates qwen3_tts_bridge_abi_layout; struct sizes match but the '
          'field layout is unverified', file=sys.stderr)
    return 'sizes'


def token_budget(text):
    """Audio tokens allowed for one sentence: proportional to its length, with a floor
    for short texts and a ceiling against runaway generation."""
    return min(MAX_TOKENS, max(MIN_TOKENS, TOKENS_PER_CHAR * len(text)))


class NativeTTS:
    def __init__(self, library, models_dir, icl_prompt=None, backend='cuda', dll_dirs=(), seed=7,
                 language='ja', cpu_threads=2, speaker=None, instruct=None):
        """``seed`` fixes the sampling so a sentence always sounds the same (reproducible
        tests, a stable voice); ``seed=None`` draws a fresh seed per sentence.

        Two ways to pick the voice: ``icl_prompt`` (a Base talker cloning a reference) or
        ``speaker`` with an optional ``instruct`` (a CustomVoice talker's named speaker, the
        instruction steering tone and pace, e.g. "语速放慢，轻快有起伏")."""
        if not icl_prompt and not speaker:
            raise ValueError('give an icl_prompt (voice clone) or a speaker (CustomVoice model)')
        if backend == 'cpu':
            os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
        self._dll_dirs = [os.add_dll_directory(d) for d in dll_dirs] if hasattr(os, 'add_dll_directory') else []
        # A fully qualified native path: with forward slashes Windows' safe DLL search
        # does not treat the path as absolute and fails to resolve the dependencies.
        lib = self.lib = C.CDLL(os.path.normpath(os.path.abspath(library)))
        self.abi_check = check_abi(lib)
        lib.qwen3_tts_init.restype = C.c_void_p
        lib.qwen3_tts_free.argtypes = [C.c_void_p]
        lib.qwen3_tts_load_models.argtypes = [C.c_void_p, C.c_char_p]
        lib.qwen3_tts_set_backend_preference.argtypes = [C.c_int32]
        lib.qwen3_tts_set_cpu_threads.argtypes = [C.c_int32]
        lib.qwen3_tts_get_active_backend_name.restype = C.c_void_p
        lib.qwen3_tts_get_last_error.argtypes = [C.c_void_p]
        lib.qwen3_tts_get_last_error.restype = C.c_void_p
        lib.qwen3_tts_free_string.argtypes = [C.c_void_p]
        lib.qwen3_tts_synthesize_with_icl_prompt_streaming.argtypes = [
            C.c_void_p, C.c_char_p, C.c_char_p, Streaming, Callback, C.c_void_p]
        lib.qwen3_tts_synthesize_with_icl_prompt_streaming.restype = Result
        lib.qwen3_tts_synthesize_streaming.argtypes = [C.c_void_p, C.c_char_p, Streaming, Callback, C.c_void_p]
        lib.qwen3_tts_synthesize_streaming.restype = Result
        lib.qwen3_tts_free_result.argtypes = [Result]
        if not lib.qwen3_tts_set_backend_preference({'cpu': 1, 'cuda': 2}[backend]):
            raise RuntimeError('backend selection failed')
        lib.qwen3_tts_set_cpu_threads(cpu_threads)
        self.ctx = lib.qwen3_tts_init()
        if not self.ctx:
            raise RuntimeError('context allocation failed')
        if not lib.qwen3_tts_load_models(self.ctx, str(models_dir).encode()):
            error = self._last_error()
            self.close()
            raise RuntimeError(f'model loading failed: {error}')
        self.active_backend = self._take_string(lib.qwen3_tts_get_active_backend_name())
        self.prompt = str(icl_prompt).encode() if icl_prompt else None
        self.speaker = speaker.encode() if speaker else None
        self.instruct = instruct.encode() if instruct else None
        self.backend, self.seed = backend, seed
        self.language_id = LANGUAGE_IDS[language]

    def _take_string(self, pointer):
        try:
            return C.string_at(pointer).decode(errors='replace') if pointer else ''
        finally:
            if pointer:
                self.lib.qwen3_tts_free_string(pointer)

    def _last_error(self):
        return self._take_string(self.lib.qwen3_tts_get_last_error(self.ctx))

    def close(self):
        if getattr(self, 'ctx', None):
            self.lib.qwen3_tts_free(self.ctx)
            self.ctx = None

    def synthesize(self, text, emit, tail=True, seed=None):
        """Stream PCM to emit(float32 ndarray); returns metrics. emit may raise BrokenPipeError to cancel.

        ``seed`` overrides the instance default for this sentence. The metrics carry
        ``token_budget`` and ``truncated``: the model hit the budget before finishing,
        so the end of the sentence is missing (the caller should log it)."""
        budget = token_budget(text)
        if seed is None:
            seed = self.seed if self.seed is not None else random.getrandbits(62)
        params = Params(budget, .9, 1., 50, 2, 0, 0, 1.05, self.language_id, self.instruct, self.speaker, 2., seed,
                        1 if self.prompt else 0)   # full-text prefill is an ICL-prompt feature
        settings = Streaming(params, .32, 6.4, 0)
        gate = TailGate() if tail else None
        started = time.perf_counter()
        metrics = dict(callbacks=[], input_samples=0, output_samples=0, cancelled=False, success=False,
                       token_budget=budget, frames=0, truncated=False, seed=seed)
        failure = []

        def write(wave):
            if len(wave):
                emit(wave)
                metrics['output_samples'] += len(wave)

        def receive(pointer, _):
            try:
                chunk = pointer.contents
                wave = np.ctypeslib.as_array(chunk.samples, shape=(chunk.n_samples,)).copy()
                metrics['callbacks'].append(dict(samples=len(wave), elapsed_seconds=time.perf_counter() - started))
                metrics['input_samples'] += len(wave)
                metrics['frames'] = max(metrics['frames'], int(chunk.end_frame))
                write(gate.feed(wave) if gate else wave)
                return 1
            except (BrokenPipeError, ConnectionResetError):
                metrics['cancelled'] = True
                return 0
            except Exception as error:  # surfaced after the native call returns
                failure.append(error)
                return 0

        callback = Callback(receive)
        if self.prompt:
            result = self.lib.qwen3_tts_synthesize_with_icl_prompt_streaming(
                self.ctx, text.encode('utf-8'), self.prompt, settings, callback, None)
        else:
            result = self.lib.qwen3_tts_synthesize_streaming(self.ctx, text.encode('utf-8'), settings, callback, None)
        try:
            if failure:
                raise failure[0]
            if not metrics['cancelled'] and not result.success:
                raise RuntimeError((result.error_msg or b'native synthesis failed').decode(errors='replace'))
            if not metrics['cancelled'] and gate:
                write(gate.finish())
            metrics['success'] = bool(result.success) and not metrics['cancelled']
            # the engine appends QWEN3_TTS_TAIL_PAD_FRAMES copies of the last frame after end-of-speech
            tail_pad = int(os.environ.get('QWEN3_TTS_TAIL_PAD_FRAMES') or 3)
            metrics['truncated'] = metrics['success'] and metrics['frames'] - tail_pad >= budget
            metrics['wall_seconds'] = time.perf_counter() - started
            return metrics
        finally:
            self.lib.qwen3_tts_free_result(result)

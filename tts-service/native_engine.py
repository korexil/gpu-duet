"""ctypes binding for the C ABI of the patched qwen3-tts.cpp bridge (qwen3_tts_bridge.dll/.so).

Streams 24 kHz float PCM through a callback while synthesizing with a pre-extracted
in-context-learning (voice clone) prompt. See native-tts/README.md for the build.
"""
import ctypes as C
import os
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


Callback = C.CFUNCTYPE(C.c_int32, C.POINTER(Chunk), C.c_void_p)

# talker_config.codec_language_id of Qwen3-TTS-12Hz-1.7B-Base
LANGUAGE_IDS = {'zh': 2055, 'en': 2050, 'de': 2053, 'it': 2070, 'pt': 2071, 'es': 2054,
                'ja': 2058, 'ko': 2064, 'fr': 2061, 'ru': 2069}


class NativeTTS:
    def __init__(self, library, models_dir, icl_prompt, backend='cuda', dll_dirs=(), seed=7,
                 max_tokens=128, language='ja', cpu_threads=2):
        if backend == 'cpu':
            os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
        self._dll_dirs = [os.add_dll_directory(d) for d in dll_dirs] if hasattr(os, 'add_dll_directory') else []
        lib = self.lib = C.CDLL(library)
        lib.qwen3_tts_bridge_abi_sizes.argtypes = [C.POINTER(C.c_int32)]
        sizes = (C.c_int32 * 4)()
        lib.qwen3_tts_bridge_abi_sizes(sizes)
        expected = [C.sizeof(t) for t in (Params, Streaming, Chunk, Result)]
        if list(sizes) != expected:
            raise RuntimeError(f'ABI mismatch: library {list(sizes)} vs binding {expected}')
        lib.qwen3_tts_init.restype = C.c_void_p
        lib.qwen3_tts_free.argtypes = [C.c_void_p]
        lib.qwen3_tts_load_models.argtypes = [C.c_void_p, C.c_char_p]
        lib.qwen3_tts_set_backend_preference.argtypes = [C.c_int32]
        lib.qwen3_tts_set_cpu_threads.argtypes = [C.c_int32]
        lib.qwen3_tts_get_active_backend_name.restype = C.c_void_p
        lib.qwen3_tts_free_string.argtypes = [C.c_void_p]
        lib.qwen3_tts_synthesize_with_icl_prompt_streaming.argtypes = [
            C.c_void_p, C.c_char_p, C.c_char_p, Streaming, Callback, C.c_void_p]
        lib.qwen3_tts_synthesize_with_icl_prompt_streaming.restype = Result
        lib.qwen3_tts_free_result.argtypes = [Result]
        if not lib.qwen3_tts_set_backend_preference({'cpu': 1, 'cuda': 2}[backend]):
            raise RuntimeError('backend selection failed')
        lib.qwen3_tts_set_cpu_threads(cpu_threads)
        self.ctx = lib.qwen3_tts_init()
        if not self.ctx:
            raise RuntimeError('context allocation failed')
        if not lib.qwen3_tts_load_models(self.ctx, str(models_dir).encode()):
            self.close()
            raise RuntimeError('model loading failed')
        name = lib.qwen3_tts_get_active_backend_name()
        try:
            self.active_backend = C.string_at(name).decode() if name else ''
        finally:
            if name:
                lib.qwen3_tts_free_string(name)
        self.prompt = str(icl_prompt).encode()
        self.backend, self.seed, self.max_tokens = backend, seed, max_tokens
        self.language_id = LANGUAGE_IDS[language]

    def close(self):
        if getattr(self, 'ctx', None):
            self.lib.qwen3_tts_free(self.ctx)
            self.ctx = None

    def synthesize(self, text, emit, tail=True):
        """Stream PCM to emit(float32 ndarray); returns metrics. emit may raise BrokenPipeError to cancel."""
        budget = max(self.max_tokens, 512) if len(text) > 40 else self.max_tokens
        params = Params(budget, .9, 1., 50, 2, 0, 0, 1.05, self.language_id, None, None, 2., self.seed, 1)
        settings = Streaming(params, .32, 6.4, 0)
        gate = TailGate() if tail else None
        started = time.perf_counter()
        metrics = dict(callbacks=[], input_samples=0, output_samples=0, cancelled=False, success=False)
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
                write(gate.feed(wave) if gate else wave)
                return 1
            except (BrokenPipeError, ConnectionResetError):
                metrics['cancelled'] = True
                return 0
            except Exception as error:  # surfaced after the native call returns
                failure.append(error)
                return 0

        callback = Callback(receive)
        result = self.lib.qwen3_tts_synthesize_with_icl_prompt_streaming(
            self.ctx, text.encode('utf-8'), self.prompt, settings, callback, None)
        try:
            if failure:
                raise failure[0]
            if not metrics['cancelled'] and not result.success:
                raise RuntimeError((result.error_msg or b'native synthesis failed').decode(errors='replace'))
            if not metrics['cancelled'] and gate:
                write(gate.finish())
            metrics['success'] = bool(result.success) and not metrics['cancelled']
            metrics['wall_seconds'] = time.perf_counter() - started
            return metrics
        finally:
            self.lib.qwen3_tts_free_result(result)

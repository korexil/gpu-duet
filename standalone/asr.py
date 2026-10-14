"""Silero VAD (onnxruntime) and SenseVoice (sherpa-onnx), both on the CPU.

Same models xiaozhi-esp32-server uses, loaded without torch or funasr: the VAD is a
2 MB onnx file, SenseVoice an int8 onnx of ~240 MB. ``setup_models.py`` fetches both.
"""
import re
from pathlib import Path

import numpy as np

_TAG = re.compile(r'<\|[^|>]*\|>')


class Vad:
    """Per-frame speech probability. Stateful: call ``reset()`` at every utterance boundary.

    The model takes 512-sample chunks (32 ms at 16 kHz) prefixed with the last 64 samples of
    the previous chunk; device frames are 960 samples, so leftovers carry over to the next frame.
    """
    CHUNK = 512
    CONTEXT = 64

    def __init__(self, model_path, threshold=0.5, threshold_low=0.2, rate=16000, threads=1):
        import onnxruntime
        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = threads
        opts.intra_op_num_threads = threads
        self.session = onnxruntime.InferenceSession(str(model_path), sess_options=opts,
                                                    providers=['CPUExecutionProvider'])
        self.threshold, self.threshold_low, self.rate = threshold, threshold_low, rate
        names = {i.name for i in self.session.get_inputs()}
        self.v5 = 'state' in names            # v5: input/state/sr; v4 (as shipped by sherpa-onnx): x/h/c/sr
        if not self.v5 and not {'x', 'h', 'c'} <= names:
            raise ValueError(f'unexpected Silero VAD inputs {sorted(names)}')
        self._needs_sr = 'sr' in names
        self.last_prob = 0.0          # highest probability seen in the last frame, for diagnostics
        self.reset()

    def reset(self):
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._h = np.zeros((2, 1, 64), dtype=np.float32)
        self._c = np.zeros((2, 1, 64), dtype=np.float32)
        self._context = np.zeros(self.CONTEXT, dtype=np.float32)
        self._tail = np.zeros(0, dtype=np.float32)
        self._voice = False

    def _prob(self, chunk):
        sr = np.array(self.rate, dtype=np.int64)
        if self.v5:
            prob, self._state = self.session.run(None, {
                'input': np.concatenate([self._context, chunk]).reshape(1, -1), 'state': self._state, 'sr': sr})
            self._context = chunk[-self.CONTEXT:]
        else:
            feed = {'x': chunk.reshape(1, -1), 'h': self._h, 'c': self._c}
            if self._needs_sr:
                feed['sr'] = sr
            prob, self._h, self._c = self.session.run(None, feed)
        return float(prob[0][0])

    def is_voice(self, frame: np.ndarray) -> bool:
        """True when any chunk of this frame is speech (with hysteresis between the two thresholds)."""
        x = np.concatenate([self._tail, frame.astype(np.float32) / 32768.0])
        hit, self.last_prob = False, 0.0
        while len(x) >= self.CHUNK:
            chunk, x = x[:self.CHUNK], x[self.CHUNK:]
            p = self._prob(chunk)
            self.last_prob = max(self.last_prob, p)
            if p >= self.threshold:
                self._voice = True
            elif p <= self.threshold_low:
                self._voice = False
            hit = hit or self._voice
        self._tail = x
        return hit


class Recognizer:
    """SenseVoice through sherpa-onnx; ``transcribe`` takes s16le 16 kHz mono and returns plain text."""

    def __init__(self, model_dir, language='auto', threads=2, rate=16000):
        import sherpa_onnx
        model_dir = Path(model_dir)
        model = model_dir / 'model.int8.onnx'
        tokens = model_dir / 'tokens.txt'
        for path in (model, tokens):
            if not path.is_file():
                raise FileNotFoundError(f'{path} missing: run setup_models.py')
        self.rate = rate
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model), tokens=str(tokens), num_threads=threads, sample_rate=rate,
            feature_dim=80, decoding_method='greedy_search', language=language, use_itn=True, debug=False)

    def transcribe(self, pcm: np.ndarray) -> str:
        stream = self.recognizer.create_stream()
        stream.accept_waveform(self.rate, pcm.astype(np.float32) / 32768.0)
        self.recognizer.decode_stream(stream)
        return clean_text(stream.result.text)


def clean_text(text: str) -> str:
    return _TAG.sub('', text or '').strip()


class Denoiser:
    """GTCRN speech enhancement through sherpa-onnx, run on a whole utterance before the ASR.

    A 0.5 MB model, about 0.1 s per second of audio on one CPU thread. Meant for a device whose
    microphone carries a broadband floor (fan, servo, the firmware's own gain riding up in silence).
    """

    def __init__(self, model_path, rate=16000, threads=1):
        import sherpa_onnx
        config = sherpa_onnx.OfflineSpeechDenoiserConfig(
            model=sherpa_onnx.OfflineSpeechDenoiserModelConfig(
                gtcrn=sherpa_onnx.OfflineSpeechDenoiserGtcrnModelConfig(model=str(model_path)),
                num_threads=threads, provider='cpu'))
        self.denoiser = sherpa_onnx.OfflineSpeechDenoiser(config)
        self.rate = rate

    def run(self, pcm: np.ndarray) -> np.ndarray:
        out = self.denoiser.run(pcm.astype(np.float32) / 32768.0, self.rate)
        clean = np.asarray(out.samples, dtype=np.float32)
        return (np.clip(clean, -1, 1) * 32767).astype(np.int16)

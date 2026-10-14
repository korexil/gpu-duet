"""Minimal ctypes binding to libopus: one encoder, one decoder, nothing else.

Windows: the DLL ships with the PyOgg wheel (``pip install pyogg``), which this module
finds on its own. Linux / macOS: the system libopus (``apt install libopus0``,
``brew install opus``). ``OPUS_LIBRARY`` overrides the search.
"""
import ctypes
import ctypes.util
import os
from pathlib import Path

APPLICATION_VOIP = 2048
APPLICATION_AUDIO = 2049
_MAX_PACKET = 4000


def _candidates():
    env = os.environ.get('OPUS_LIBRARY')
    if env:
        yield env
    here = Path(__file__).resolve().parent
    for name in ('opus.dll', 'libopus.dll', 'libopus.so', 'libopus.dylib'):
        yield str(here / name)
    try:
        import pyogg
        yield str(Path(pyogg.__file__).resolve().parent / 'opus.dll')
    except ImportError:
        pass
    for name in ('opus', 'libopus', 'libopus.so.0', 'libopus.0.dylib'):
        found = ctypes.util.find_library(name)
        if found:
            yield found
    yield 'libopus.so.0'


def load_library():
    for candidate in _candidates():
        if os.path.isabs(candidate) and not os.path.exists(candidate):
            continue
        try:
            lib = ctypes.CDLL(candidate)
        except OSError:
            continue
        lib.opus_encoder_create.restype = ctypes.c_void_p
        lib.opus_encoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        lib.opus_encode.restype = ctypes.c_int32
        lib.opus_encode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int16), ctypes.c_int,
                                    ctypes.c_char_p, ctypes.c_int32]
        lib.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
        lib.opus_decoder_create.restype = ctypes.c_void_p
        lib.opus_decoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        lib.opus_decode.restype = ctypes.c_int
        lib.opus_decode.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int32,
                                    ctypes.POINTER(ctypes.c_int16), ctypes.c_int, ctypes.c_int]
        lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]
        lib.opus_strerror.restype = ctypes.c_char_p
        lib.opus_strerror.argtypes = [ctypes.c_int]
        return lib
    raise OSError('libopus not found: pip install pyogg (Windows), install libopus (Linux/macOS), '
                  'or point OPUS_LIBRARY at the shared library')


class OpusError(RuntimeError):
    pass


class Encoder:
    def __init__(self, lib, rate, channels=1, application=APPLICATION_AUDIO):
        self.lib, self.channels = lib, channels
        err = ctypes.c_int()
        self.state = lib.opus_encoder_create(rate, channels, application, ctypes.byref(err))
        if err.value or not self.state:
            raise OpusError(lib.opus_strerror(err.value).decode())
        self._out = ctypes.create_string_buffer(_MAX_PACKET)

    def encode(self, pcm: bytes, frame_size: int) -> bytes:
        """``pcm`` is s16le with exactly ``frame_size`` samples per channel."""
        if len(pcm) != frame_size * self.channels * 2:
            raise ValueError(f'expected {frame_size * self.channels * 2} bytes, got {len(pcm)}')
        buf = (ctypes.c_int16 * (frame_size * self.channels)).from_buffer_copy(pcm)
        n = self.lib.opus_encode(self.state, buf, frame_size, self._out, _MAX_PACKET)
        if n < 0:
            raise OpusError(self.lib.opus_strerror(n).decode())
        return self._out.raw[:n]

    def __del__(self):
        if getattr(self, 'state', None):
            self.lib.opus_encoder_destroy(self.state)
            self.state = None


class Decoder:
    def __init__(self, lib, rate, channels=1):
        self.lib, self.channels = lib, channels
        err = ctypes.c_int()
        self.state = lib.opus_decoder_create(rate, channels, ctypes.byref(err))
        if err.value or not self.state:
            raise OpusError(lib.opus_strerror(err.value).decode())

    def decode(self, packet: bytes, frame_size: int) -> bytes:
        buf = (ctypes.c_int16 * (frame_size * self.channels))()
        n = self.lib.opus_decode(self.state, packet, len(packet), buf, frame_size, 0)
        if n < 0:
            raise OpusError(self.lib.opus_strerror(n).decode())
        return bytes(memoryview(buf).cast('B')[:n * self.channels * 2])

    def __del__(self):
        if getattr(self, 'state', None):
            self.lib.opus_decoder_destroy(self.state)
            self.state = None


class OpusCodec:
    """What the session needs: decode device frames at ``in_rate``, encode replies at ``out_rate``."""

    def __init__(self, in_rate=16000, out_rate=24000, lib=None):
        lib = lib or load_library()
        self.decoder = Decoder(lib, in_rate)
        self.encoder = Encoder(lib, out_rate)

    def decode(self, packet, frame_size):
        return self.decoder.decode(packet, frame_size)

    def encode(self, pcm, frame_size):
        return self.encoder.encode(pcm, frame_size)


class RawCodec:
    """Identity codec for tests and loopback tools: a 'packet' is the s16le frame itself."""

    def decode(self, packet, frame_size):
        return bytes(packet)

    def encode(self, pcm, frame_size):
        return bytes(pcm)

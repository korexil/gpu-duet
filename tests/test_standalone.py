"""The standalone server without models, GPU or a device: a fake device speaks the xiaozhi
protocol over a real websocket; VAD, ASR, LLM and TTS are stand-ins; the codec is identity."""
import asyncio
import json
import sys
import time
import urllib.request

import numpy as np
import pytest
import websockets

from conftest import ROOT, load_module

# tts-service has a server.py too, so the standalone entry point is loaded under its own name
standalone = load_module('standalone_server', 'standalone/server.py')
sys.path.insert(0, str(ROOT / 'standalone'))
from brain import SentenceSplitter  # noqa: E402
from opus_codec import RawCodec  # noqa: E402

FRAME = 960          # 60 ms at 16 kHz
OUT_FRAME = 1440     # 60 ms at 24 kHz


class AmplitudeVad:
    def reset(self):
        pass

    def is_voice(self, frame):
        return bool(np.abs(frame).max() > 1000)


class FakeAsr:
    def __init__(self):
        self.calls = []

    def transcribe(self, pcm):
        self.calls.append(len(pcm))
        return '<|zh|>你好啊<|NEUTRAL|>' if len(pcm) else ''


class FakeBrain:
    base_url = 'http://llm'

    def __init__(self, text='第一句。第二句！', delay=0.0):
        self.text, self.delay, self.dialogues, self.closed = text, delay, [], 0

    def reply(self, dialogue):
        self.dialogues.append(dialogue)
        try:
            for ch in self.text:
                if self.delay:
                    time.sleep(self.delay)
                yield ch
        finally:
            self.closed += 1

    def health(self):
        return True


class FakeTts:
    base_url = 'http://tts'

    def __init__(self, seconds=0.3):
        self.seconds, self.sentences, self.pauses = seconds, [], []

    def stream(self, text, pause=None):
        self.sentences.append(text)
        self.pauses.append(pause)
        yield np.full(int(self.seconds * 24000), 1000, dtype=np.int16).tobytes()

    def health(self):
        return {'busy': False, 'started': 0, 'served': 0}


def make_cfg(**voice):
    cfg = standalone.merge(standalone.DEFAULTS, {'listen': {'host': '127.0.0.1', 'port': 0, 'http_port': 0}})
    cfg['voice'].update({'end_silence_ms': 120, 'min_voice_ms': 120, 'preroll_ms': 60, 'guard_ms': 100, **voice})
    cfg['wake']['words'] = ['你好小智']
    cfg['splitter'] = {'min_chars': 0, 'max_chars': 60, 'first_cut_chars': 0}   # the fakes count sentences
    return cfg


@pytest.fixture
def stack():
    return dict(asr=FakeAsr(), brain=FakeBrain(), tts=FakeTts())


@pytest.fixture
def ctx(stack):
    return standalone.Context(make_cfg(), codec_factory=RawCodec, vad_factory=AmplitudeVad, **stack)


async def start(ctx):
    """Run the server in the background; the returned stop() cancels it and waits for a clean close."""
    ready = asyncio.get_running_loop().create_future()
    task = asyncio.create_task(standalone.run_server(ctx, ready))
    server = await ready
    port = server.sockets[0].getsockname()[1]

    async def stop():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return stop, port


class Device:
    def __init__(self, ws):
        self.ws, self.json, self.audio = ws, [], []

    async def hello(self):
        await self.ws.send(json.dumps({'type': 'hello', 'version': 1, 'transport': 'websocket',
                                       'audio_params': {'format': 'opus', 'sample_rate': 16000, 'frame_duration': 60}}))
        reply = json.loads(await self.ws.recv())
        assert reply['type'] == 'hello' and reply['transport'] == 'websocket'
        return reply

    async def say(self, voiced_frames, silent_frames=4):
        loud = np.full(FRAME, 5000, dtype=np.int16).tobytes()
        quiet = bytes(FRAME * 2)
        for _ in range(voiced_frames):
            await self.ws.send(loud)
        for _ in range(silent_frames):
            await self.ws.send(quiet)

    async def collect(self, until_state='stop', timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = await asyncio.wait_for(self.ws.recv(), timeout=max(0.01, deadline - time.monotonic()))
            if isinstance(msg, bytes):
                self.audio.append(msg)
                continue
            data = json.loads(msg)
            self.json.append(data)
            if data.get('type') == 'tts' and data.get('state') == until_state:
                return
        raise TimeoutError(f'no tts {until_state}')


async def run_device(port, scenario):
    async with websockets.connect(f'ws://127.0.0.1:{port}/xiaozhi/v1/',
                                  additional_headers={'Device-Id': 'aa:bb', 'Protocol-Version': '1'}) as ws:
        device = Device(ws)
        await device.hello()
        await scenario(device)
        return device


def test_utterance_goes_through_asr_llm_tts_and_back(ctx, stack):
    async def main():
        stop, port = await start(ctx)
        try:
            async def scenario(device):
                await device.say(10)
                await device.collect('stop')
            device = await run_device(port, scenario)
        finally:
            await stop()
        return device

    device = asyncio.run(main())
    states = [(m['type'], m.get('state'), m.get('text')) for m in device.json]
    assert ('stt', None, '你好啊') in states                       # ASR text shown on the device, tags stripped
    assert states.index(('tts', 'start', None)) < states.index(('tts', 'sentence_start', '第一句。'))
    assert ('tts', 'sentence_start', '第二句！') in states
    assert states[-1] == ('tts', 'stop', None)
    assert stack['tts'].sentences == ['第一句。', '第二句！']
    assert stack['brain'].dialogues[0][-1] == {'role': 'user', 'content': '你好啊'}
    assert stack['brain'].dialogues[0][0]['role'] == 'system'
    # 2 x 0.3 s of PCM at 24 kHz, cut into 60 ms frames: 10 frames, each exactly one frame long
    assert len(device.audio) == 10 and all(len(f) == OUT_FRAME * 2 for f in device.audio)
    # the ASR got the 10 voiced frames plus the 2 silent ones (end_silence_ms=120) that ended the utterance
    assert stack['asr'].calls == [FRAME * 12]


def test_microphone_is_ignored_while_speaking_and_during_the_guard(stack):
    ctx = standalone.Context(make_cfg(guard_ms=300), codec_factory=RawCodec, vad_factory=AmplitudeVad, **stack)
    stack['brain'].delay = 0.02  # a slow answer, so the device can talk over it

    async def main():
        stop, port = await start(ctx)
        try:
            async def scenario(device):
                await device.say(10)
                await device.collect('start')
                await device.say(10)               # talking over the reply: must not become a second turn
                await device.collect('stop')
                await device.say(10)               # inside the guard window: still ignored
                await asyncio.sleep(0.4)
                await device.say(10)               # after the guard: heard
                await device.collect('stop')
            await run_device(port, scenario)
        finally:
            await stop()

    asyncio.run(main())
    assert len(stack['asr'].calls) == 2
    assert len(stack['brain'].dialogues) == 2


def test_wake_word_plays_the_cached_ack_and_device_text_is_chatted(stack):
    ack = [bytes(OUT_FRAME * 2)] * 3
    ctx = standalone.Context(make_cfg(), codec_factory=RawCodec, vad_factory=AmplitudeVad,
                             wake_ack=(ack, '嗯？'), **stack)

    async def main():
        stop, port = await start(ctx)
        try:
            async def scenario(device):
                await device.ws.send(json.dumps({'type': 'listen', 'state': 'detect', 'text': '你好小智'}))
                await device.collect('stop')
                assert len(device.audio) == 3
                await device.ws.send(json.dumps({'type': 'listen', 'state': 'detect', 'text': '（摸了摸头）'}))
                await device.collect('stop')
            await run_device(port, scenario)
        finally:
            await stop()

    asyncio.run(main())
    assert stack['asr'].calls == []
    assert stack['brain'].dialogues[0][-1]['content'] == '（摸了摸头）'


def test_abort_from_the_device_stops_the_reply(stack):
    stack['brain'].delay = 0.05
    stack['brain'].text = '一。二。三。四。五。六。七。八。'
    ctx = standalone.Context(make_cfg(), codec_factory=RawCodec, vad_factory=AmplitudeVad, **stack)

    async def main():
        stop, port = await start(ctx)
        try:
            async def scenario(device):
                await device.say(10)
                await device.collect('sentence_start')
                await device.ws.send(json.dumps({'type': 'abort', 'reason': 'wake_word_detected'}))
                await device.collect('stop', timeout=2)
            await run_device(port, scenario)
        finally:
            await stop()

    asyncio.run(main())
    assert len(stack['tts'].sentences) < 8


def test_ota_and_status_over_http(ctx):
    async def main():
        stop, port = await start(ctx)
        try:
            def post_ota():
                request = urllib.request.Request(f'http://127.0.0.1:{ctx.http_port}/xiaozhi/ota/',
                                                 data=b'{"application":{"version":"1.0.0"}}',
                                                 headers={'Content-Type': 'application/json', 'Host': '192.168.1.5:8003'})
                with urllib.request.urlopen(request, timeout=5) as response:
                    return json.loads(response.read())

            def get_status():
                with urllib.request.urlopen(f'http://127.0.0.1:{ctx.http_port}/health', timeout=5) as response:
                    return json.loads(response.read())
            body = await asyncio.to_thread(post_ota)
            health = await asyncio.to_thread(get_status)
        finally:
            await stop()
        return body, port, health

    body, port, health = asyncio.run(main())
    assert body['websocket']['url'] == f'ws://192.168.1.5:{port}/xiaozhi/v1/'
    assert 'server_time' in body and body['firmware']['url'] == ''
    assert health['ok'] and health['llm']['up'] and health['tts']['up']


def test_sentence_splitter_cuts_first_clause_early_and_not_inside_quotes():
    s = SentenceSplitter(first_cut_chars=6, max_chars=20, min_chars=0)
    assert s.feed('今天天气不错，') == ['今天天气不错，']
    assert s.feed('我们去「公园，湖边」') == []
    assert s.feed('走走吧。好的') == ['我们去「公园，湖边」走走吧。']
    assert s.flush() == '好的'
    assert s.flush() is None
    s = SentenceSplitter(first_cut_chars=100, max_chars=10, min_chars=0)
    assert s.feed('一二三四五六七八九十，十一。') == ['一二三四五六七八九十，', '十一。']


def test_sentence_splitter_keeps_a_short_reply_whole_until_min_chars():
    s = SentenceSplitter(min_chars=20, max_chars=100)
    assert s.feed('第一句。第二句！') == []                # under min_chars: no cut at a sentence end
    assert s.feed('第三句要比前两句长一点点。') == ['第一句。第二句！第三句要比前两句长一点点。']   # 21 chars: cut
    assert s.feed('尾巴') == [] and s.flush() == '尾巴'
    s = SentenceSplitter(min_chars=20, max_chars=100)
    assert s.feed('换行也算段落结束\n下一段') == ['换行也算段落结束']   # a newline always cuts


def test_config_merge_keeps_defaults_and_resolves_model_paths(tmp_path):
    config = tmp_path / 'config.yaml'
    config.write_text('llm:\n  max_tokens: 99\nasr:\n  model_dir: m\n', encoding='utf-8')
    cfg = standalone.load_config(config)
    assert cfg['llm']['max_tokens'] == 99 and cfg['llm']['chunk_tokens'] == 16
    assert cfg['asr']['model_dir'] == str((tmp_path / 'm').resolve())
    assert cfg['asr']['vad_model'].endswith('silero_vad.onnx')


def test_opus_roundtrip_if_the_library_is_available():
    opus = pytest.importorskip('opus_codec')
    try:
        lib = opus.load_library()
    except OSError:
        pytest.skip('libopus not installed')
    encoder, decoder = opus.Encoder(lib, 24000), opus.Decoder(lib, 24000)
    t = np.arange(OUT_FRAME) / 24000
    pcm = (np.sin(2 * np.pi * 440 * t) * 8000).astype(np.int16).tobytes()
    packet = encoder.encode(pcm, OUT_FRAME)
    assert 0 < len(packet) < len(pcm)
    out = np.frombuffer(decoder.decode(packet, OUT_FRAME), dtype=np.int16)
    assert len(out) == OUT_FRAME


def test_agc_evens_out_a_loud_opening_and_a_quiet_tail():
    from agc import Agc
    rate, n = 24000, 1440
    rng = np.random.default_rng(0)
    loud = (rng.standard_normal((20, n)) * 0.30 * 32767).astype(np.int16)    # ~-10 dBFS
    quiet = (rng.standard_normal((40, n)) * 0.03 * 32767).astype(np.int16)   # ~-30 dBFS
    agc = Agc(rate)
    out = [agc.process(f) for f in np.concatenate([loud, quiet])]
    rms = [20 * np.log10(np.sqrt(np.mean((f / 32768.0) ** 2)) + 1e-9) for f in out]
    assert rms[0] > rms[1] > rms[2]                  # the loud opening is pulled down within a few frames
    assert abs(rms[10] + 20) < 1                     # and sits at the target
    assert rms[-1] > rms[20] + 4                     # the quiet tail is brought up, slowly
    assert all(np.abs(f).max() <= 32767 * 10 ** (-3 / 20) + 1 for f in out)   # ceiling holds


class TrailingSilenceTts(FakeTts):
    """0.3 s of tone followed by 0.5 s of silence per chunk, like tts-service's sentence pause."""

    def stream(self, text, pause=None):
        self.sentences.append(text)
        self.pauses.append(pause)
        tone = np.full(int(0.3 * 24000), 1000, dtype=np.int16).tobytes()
        yield tone[:len(tone) // 2]
        yield tone[len(tone) // 2:] + bytes(2 * 12000)


def test_the_pause_is_requested_from_the_tts_and_nothing_is_held_back(stack):
    stack['tts'] = TrailingSilenceTts()
    ctx = standalone.Context(make_cfg(chunk_pause_ms=120), codec_factory=RawCodec, vad_factory=AmplitudeVad, **stack)

    async def main():
        stop, port = await start(ctx)
        try:
            async def scenario(device):
                await device.say(10)
                await device.collect('stop')
            device = await run_device(port, scenario)
        finally:
            await stop()
        return device

    device = asyncio.run(main())
    pcm = np.frombuffer(b''.join(device.audio), dtype=np.int16)
    # the TTS is told to leave 0.12 s after each sentence; whatever it sends is forwarded whole
    # (two sentences of 0.3 s tone + 0.5 s silence here), padded to a frame at the end
    assert stack['tts'].pauses == [0.12, 0.12]
    assert abs(len(pcm) / 24000 - 2 * 0.8) < 0.07
    assert np.abs(pcm[:7000]).max() > 500


def test_private_endpoints_need_this_machine_or_the_token():
    allowed = standalone.private_allowed
    lan = '192.168.1.20'
    assert allowed({'token': ''}, '127.0.0.1', None, '/dashboard')
    assert allowed({'token': ''}, '::1', None, '/events')
    assert not allowed({'token': '', 'dashboard': {'remote': False}}, lan, None, '/dashboard')
    assert allowed({'token': '', 'dashboard': {'remote': True}}, lan, None, '/dashboard')
    cfg = {'token': 's3cret', 'dashboard': {'remote': True}}       # with a token, remote: true does not open it
    assert not allowed(cfg, lan, None, '/events')
    assert not allowed(cfg, lan, 'Bearer wrong', '/events')
    assert allowed(cfg, lan, 'Bearer s3cret', '/events')
    assert allowed(cfg, lan, None, '/events?token=s3cret')
    assert not allowed(cfg, lan, None, '/events?token=s3cre')


def test_websocket_token_check_uses_the_same_rule():
    assert standalone.token_ok({'token': 'abc'}, 'Bearer abc', '')
    assert not standalone.token_ok({'token': 'abc'}, 'Bearer abd', '')
    assert not standalone.token_ok({'token': ''}, 'Bearer ', '')

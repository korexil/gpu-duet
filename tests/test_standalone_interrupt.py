"""Interrupting an answer and TTS failures, without models or a device: the next answer starts
clean, nothing of the old one leaks into it, and a dead or hung TTS can't keep the LLM waiting."""
import asyncio
import json
import socket
import threading
import time

import httpx
import numpy as np
import pytest

from test_standalone import AmplitudeVad, FakeAsr, FakeBrain, FakeTts, RawCodec, make_cfg, run_device, standalone, start
from tts_client import TtsClient


def drain(bus_queue):
    events = []
    while not bus_queue.empty():
        events.append(bus_queue.get_nowait())
    return events


def run_scenario(ctx, scenario):
    async def main():
        stop, port = await start(ctx)
        try:
            return await run_device(port, scenario)
        finally:
            await stop()
    return asyncio.run(main())


def make_ctx(stack):
    return standalone.Context(make_cfg(), codec_factory=RawCodec, vad_factory=AmplitudeVad, **stack)


def test_abort_then_a_new_question_keeps_the_two_answers_apart():
    stack = dict(asr=FakeAsr(), brain=FakeBrain(text='一。二。三。四。五。六。七。八。', delay=0.03), tts=FakeTts(seconds=0.2))
    ctx = make_ctx(stack)
    events = ctx.bus.subscribe()

    async def scenario(device):
        await device.say(10)
        await device.collect('sentence_start')
        await device.ws.send(json.dumps({'type': 'abort', 'reason': 'wake_word_detected'}))
        await device.collect('stop', timeout=2)
        await device.ws.send(json.dumps({'type': 'listen', 'state': 'detect', 'text': '第二个问题'}))
        await device.collect('stop', timeout=8)
    run_scenario(ctx, scenario)

    evs = drain(events)
    questions = [i for i, e in enumerate(evs) if e['type'] == 'question']
    assert [evs[i]['gen'] for i in questions] == [1, 2]
    after = evs[questions[1]:]
    leaked = [e['type'] for e in after if e.get('gen') == 1 and e['type'] in ('tts_start', 'sentence', 'pcm', 'release', 'frame')]
    assert leaked == []
    assert any(e['type'] == 'frame' and e['gen'] == 2 for e in after)
    assert [e['gen'] for e in evs if e['type'] == 'stop'] == [1, 2]


class FailingTts(FakeTts):
    """The second sentence fails before any audio, like a 500 from tts-service."""

    def stream(self, text, pause=None):
        self.sentences.append(text)
        self.pauses.append(pause)
        if len(self.sentences) == 2:
            raise RuntimeError('synthesis failed')
        yield np.full(int(0.2 * 24000), 1000, dtype=np.int16).tobytes()


def test_a_tts_error_ends_the_answer_and_closes_the_llm_stream():
    stack = dict(asr=FakeAsr(), brain=FakeBrain(text='一。二。三。四。'), tts=FailingTts())
    ctx = make_ctx(stack)

    async def scenario(device):
        await device.say(10)
        await device.collect('stop', timeout=3)
    device = run_scenario(ctx, scenario)

    assert stack['tts'].sentences == ['一。', '二。']     # nothing is requested after the failure
    assert stack['brain'].closed == 1                       # the LLM stream was closed, not left waiting
    assert device.json[-1]['state'] == 'stop'


class BlockingTts(FakeTts):
    """One block, then no more bytes until released: a hung service as the worker thread sees it."""

    def __init__(self):
        super().__init__(seconds=0.2)
        self.release = threading.Event()

    def stream(self, text, pause=None):
        self.sentences.append(text)
        self.pauses.append(pause)
        yield np.full(int(0.2 * 24000), 1000, dtype=np.int16).tobytes()
        self.release.wait(5)


def test_abort_does_not_wait_for_a_blocked_tts():
    tts = BlockingTts()
    stack = dict(asr=FakeAsr(), brain=FakeBrain(text='一。二。'), tts=tts)
    ctx = make_ctx(stack)
    waited = []

    async def scenario(device):
        await device.say(10)
        await device.collect('sentence_start')
        await asyncio.sleep(0.3)                       # the worker is now stuck inside stream()
        started = time.monotonic()
        await device.ws.send(json.dumps({'type': 'abort', 'reason': 'button'}))
        await device.collect('stop', timeout=2)
        waited.append(time.monotonic() - started)
    try:
        run_scenario(ctx, scenario)
    finally:
        tts.release.set()
    assert waited[0] < 1.0


def test_tts_client_gives_up_on_a_service_that_sends_nothing():
    server = socket.socket()
    server.bind(('127.0.0.1', 0))
    server.listen(1)
    accepted = []
    threading.Thread(target=lambda: accepted.append(server.accept()[0]), daemon=True).start()   # never answers
    client = TtsClient(f'http://127.0.0.1:{server.getsockname()[1]}', read_timeout=0.3)
    started = time.monotonic()
    with pytest.raises(httpx.TimeoutException):
        list(client.stream('x', pause=0.4))
    assert time.monotonic() - started < 3
    server.close()

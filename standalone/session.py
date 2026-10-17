"""One device connection: the xiaozhi websocket protocol, half-duplex turn taking, and the
listen -> ASR -> LLM -> TTS -> speak loop.

Protocol (xiaozhi-esp32, protocol version 1): the device sends ``hello`` and expects ours back
within 10 s; binary frames are bare Opus packets (16 kHz, 60 ms up; our rate down, declared in
hello); audio we send must sit between ``{"type":"tts","state":"start"}`` and ``"stop"``.
``listen`` start/stop/detect, ``abort`` and ``mcp`` come as JSON.

Half duplex: while we speak, and for ``guard_ms`` after, microphone frames are dropped, so the
device never hears itself whatever its AEC setting. ``barge_in`` opts back into interruption
for devices that cancel their own echo.
"""
import asyncio
import json
import logging
import queue as queue_module
import re
import threading
import time
import uuid

import numpy as np

from agc import Agc
from asr import clean_text
from brain import Dialogue, SentenceSplitter

log = logging.getLogger('standalone')
_PUNCT = re.compile(r'[\s\W_]+', re.UNICODE)
PREBUFFER_FRAMES = 5


def normalize(text):
    return _PUNCT.sub('', text or '').lower()


class Pacer:
    """Send one 60 ms frame per 60 ms after a short pre-buffer; re-anchor after a gap."""

    def __init__(self, frame_s, prebuffer=PREBUFFER_FRAMES):
        self.frame_s, self.prebuffer = frame_s, prebuffer
        self.t0, self.sent = None, 0

    async def slot(self):
        now = time.monotonic()
        if self.t0 is None:
            self.t0 = now
        due = self.t0 + (self.sent - self.prebuffer) * self.frame_s
        gap = 0.0
        if now - due > self.prebuffer * self.frame_s:   # fell behind (TTS gap): pre-buffer again
            gap = now - due - self.prebuffer * self.frame_s   # how long the device's buffer sat empty
            self.t0 = now - self.sent * self.frame_s
            due = now - self.prebuffer * self.frame_s
        if due > now:
            await asyncio.sleep(due - now)
        self.sent += 1
        return gap

    async def drain(self):
        """Wait until everything sent so far has been played."""
        if self.t0 is not None:
            await asyncio.sleep(max(0.0, self.t0 + self.sent * self.frame_s - time.monotonic()))


class Session:
    IN_RATE, FRAME_MS = 16000, 60

    def __init__(self, ws, ctx):
        self.ws, self.ctx, self.cfg = ws, ctx, ctx.cfg
        self.sid = uuid.uuid4().hex[:12]
        request = getattr(ws, 'request', None)
        self.device_id = request.headers.get('Device-Id', '?') if request else '?'
        self.codec = ctx.make_codec()
        self.vad = ctx.make_vad()
        self.out_rate = ctx.out_rate
        self.in_frame = self.IN_RATE * self.FRAME_MS // 1000
        self.out_frame = self.out_rate * self.FRAME_MS // 1000
        self.dialogue = Dialogue(self.cfg['prompt'], self.cfg['history_turns'])
        voice = self.cfg['voice']
        self.end_silence_frames = max(1, voice['end_silence_ms'] // self.FRAME_MS)
        self.min_voice_frames = max(1, voice['min_voice_ms'] // self.FRAME_MS)
        self.preroll_frames = max(0, voice['preroll_ms'] // self.FRAME_MS)
        self.max_utterance_s = voice['max_utterance_s']
        self.guard_s = voice['guard_ms'] / 1000
        self.barge_in = bool(voice.get('barge_in'))
        self.barge_frames_needed = max(1, voice.get('barge_in_ms', 600) // self.FRAME_MS)
        self.idle_close_s = self.cfg['idle_close_s']
        self.pause_s = int(voice.get('chunk_pause_ms', 400)) / 1000   # silence the TTS is asked to leave after a segment
        agc = dict(voice.get('agc') or {})
        enabled = agc.pop('enabled', True)
        self.agc = Agc(self.out_rate, **agc) if enabled else None
        wake = self.cfg['wake']
        self.wake_words = {normalize(w) for w in wake.get('words') or [] if normalize(w)}
        self.device_text = wake.get('device_text', 'chat')

        self.frames, self.preroll = [], []
        self.voiced_frames = self.silent_frames = self.barge_count = 0
        self.utterance_started = 0.0
        self.speaking, self.guard_until = False, 0.0
        self.last_voice_at = time.monotonic()
        self.answer = None            # asyncio task of the current reply
        self.gen = 0                  # counts answers; every event of an answer carries its number
        self.cancel = threading.Event()
        self.listen_mode = None
        self.closed = False
        self._stats = [0, 0, 0, 0.0, time.monotonic()]   # frames, voiced, peak, max vad prob, since

    # ---- plumbing -----------------------------------------------------------------------
    def log(self, msg, *args, level=logging.INFO):
        log.log(level, '[%s] ' + msg, self.sid, *args)

    async def send(self, obj):
        await self.ws.send(json.dumps({**obj, 'session_id': self.sid}, ensure_ascii=False))

    async def send_tts(self, state, text=None):
        msg = {'type': 'tts', 'state': state}
        if text is not None:
            msg['text'] = text
        await self.send(msg)
        if state == 'sentence_start':
            self.ctx.bus.publish({'type': 'sentence', 'text': text, 'gen': self.gen})
        elif state == 'stop':
            self.ctx.bus.publish({'type': 'stop', 'gen': self.gen})

    async def run(self):
        self.log('device %s connected from %s', self.device_id, self.ws.remote_address)
        try:
            async for msg in self.ws:
                if isinstance(msg, bytes):
                    await self.on_audio(msg)
                else:
                    try:
                        data = json.loads(msg)
                    except ValueError:
                        continue
                    await self.on_json(data)
        finally:
            self.closed = True
            await self._stop_answer()
            self.log('disconnected (code=%s)', getattr(self.ws, 'close_code', None))

    # ---- JSON messages --------------------------------------------------------------------
    async def on_json(self, m):
        t = m.get('type')
        if t == 'hello':
            await self.send({
                'type': 'hello', 'version': 1, 'transport': 'websocket',
                'audio_params': {'format': 'opus', 'sample_rate': self.out_rate,
                                 'channels': 1, 'frame_duration': self.FRAME_MS},
            })
            self.log('hello: device audio %s, features %s', m.get('audio_params'), m.get('features'))
        elif t == 'listen':
            await self.on_listen(m)
        elif t == 'abort':
            self.log('device aborted (%s)', m.get('reason'))
            await self._stop_answer()
        elif t in ('mcp', 'iot'):
            self.log('%s message ignored', t, level=logging.DEBUG)
        else:
            self.log('unhandled message type %r', t, level=logging.DEBUG)

    async def on_listen(self, m):
        state, text = m.get('state'), (m.get('text') or '').strip()
        if state == 'start':
            self.listen_mode = m.get('mode')
            self._reset_listen()
            self.log('listen start (mode=%s)', self.listen_mode, level=logging.DEBUG)
        elif state == 'stop':
            if self.voiced_frames:
                await self._finish_utterance()
            else:
                self._reset_listen()
        elif state == 'detect':
            self.last_voice_at = time.monotonic()
            if text and normalize(text) in self.wake_words:
                self.log('wake word %r', text)
                await self._stop_answer()
                await self.play_wake_ack()
            elif text and self.device_text == 'chat':
                self.log('device text %r', text)
                await self.chat(text)
            elif text:
                self.log('device text ignored: %r', text)
            self._reset_listen()

    # ---- audio in -------------------------------------------------------------------------
    async def on_audio(self, packet):
        try:
            pcm = self.codec.decode(packet, self.in_frame)
        except Exception as error:  # a bad packet is not worth a disconnect
            self.log('decode failed: %s', error, level=logging.DEBUG)
            return
        frame = np.frombuffer(pcm, dtype=np.int16)
        now = time.monotonic()
        if self.speaking:
            if self.barge_in:
                await self._maybe_barge_in(frame)
            return
        if now < self.guard_until:
            # echo guard after speaking: not judged, but kept as pre-roll so a quick reply keeps its first word
            self.preroll.append(frame)
            if len(self.preroll) > self.preroll_frames:
                del self.preroll[:len(self.preroll) - self.preroll_frames]
            return
        voiced = self.vad.is_voice(frame)
        self._audio_stats(frame, voiced, now)
        if voiced:
            self.last_voice_at = now
        if not self.frames:
            if not voiced:
                self.preroll.append(frame)
                if len(self.preroll) > self.preroll_frames:
                    del self.preroll[:len(self.preroll) - self.preroll_frames]
                if self.idle_close_s and now - self.last_voice_at > self.idle_close_s and self.answer is None:
                    self.log('no voice for %ss, closing', self.idle_close_s)
                    await self.ws.close(1000, 'idle')
                return
            self.frames = list(self.preroll)
            self.preroll.clear()
            self.utterance_started = now
        self.frames.append(frame)
        if voiced:
            self.voiced_frames += 1
            self.silent_frames = 0
        else:
            self.silent_frames += 1
        if self.silent_frames >= self.end_silence_frames or now - self.utterance_started > self.max_utterance_s:
            await self._finish_utterance()

    def _audio_stats(self, frame, voiced, now):
        st = self._stats
        st[0] += 1
        st[1] += voiced
        st[2] = max(st[2], int(np.abs(frame).max()))
        st[3] = max(st[3], getattr(self.vad, 'last_prob', 0.0))
        if not voiced:   # spectrum of the background, to name a hum (servo, mains, fan) when there is one
            spectrum = np.abs(np.fft.rfft(frame.astype(np.float32) * np.hanning(len(frame)))) ** 2
            self._noise = spectrum if getattr(self, '_noise', None) is None else self._noise + spectrum
        if now - st[4] >= 5:
            hum = ''
            noise = getattr(self, '_noise', None)
            if noise is not None and len(noise) > 2:
                bins = np.fft.rfftfreq(self.in_frame, 1 / self.IN_RATE)
                top = int(np.argmax(noise[1:]) + 1)
                share = float(noise[top] / (noise.sum() + 1e-9))
                hum = f', background peak {bins[top]:.0f} Hz ({share:.0%} of energy)'
                self._noise = None
            self.log('audio: %d frames in %.0fs, %d voiced, peak %d, max vad %.2f%s', st[0], now - st[4], st[1],
                     st[2], st[3], hum, level=logging.DEBUG)
            self._stats = [0, 0, 0, 0.0, now]

    async def _maybe_barge_in(self, frame):
        if self.vad.is_voice(frame):
            self.barge_count += 1
            if self.barge_count >= self.barge_frames_needed:
                self.log('barge-in: user speaking, stopping')
                await self._stop_answer()
        else:
            self.barge_count = 0

    def _reset_listen(self):
        self.frames, self.preroll = [], []
        self.voiced_frames = self.silent_frames = self.barge_count = 0
        self.vad.reset()

    async def _finish_utterance(self):
        frames, voiced = self.frames, self.voiced_frames
        self._reset_listen()
        if voiced < self.min_voice_frames:
            return
        pcm = np.concatenate(frames)
        t0 = time.monotonic()
        try:
            if self.ctx.denoiser is not None:
                pcm = await asyncio.to_thread(self.ctx.denoiser.run, pcm)
            text = clean_text(await asyncio.to_thread(self.ctx.asr.transcribe, pcm))
        except Exception as error:
            self.log('ASR failed: %s', error, level=logging.ERROR)
            return
        self.log('heard %.1fs -> %r (asr %.2fs)', len(pcm) / self.IN_RATE, text, time.monotonic() - t0)
        if normalize(text) in self.wake_words:
            # the firmware only reports its wake word while idle; said mid-session it arrives as audio
            await self._stop_answer()
            await self.play_wake_ack()
        elif normalize(text):
            await self.chat(text)

    # ---- reply ----------------------------------------------------------------------------
    async def chat(self, text):
        await self._stop_answer()
        self.gen += 1
        self.dialogue.user(text)
        await self.send({'type': 'stt', 'text': text})
        self.ctx.bus.publish({'type': 'question', 'text': text, 'gen': self.gen})
        self.answer = asyncio.create_task(self._answer())

    async def _stop_answer(self):
        task, self.answer = self.answer, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _answer(self):
        self.speaking, self.cancel = True, threading.Event()
        cancel = self.cancel
        queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        gen = self.gen
        spoken, pacer, tail, t0 = [], Pacer(self.FRAME_MS / 1000), b'', time.monotonic()
        if self.agc is not None:
            self.agc.reset()
        worker = threading.Thread(target=self._produce, args=(self.dialogue.render(), queue, cancel, loop, gen),
                                  daemon=True, name=f'answer-{self.sid}')
        await self.send_tts('start')
        worker.start()
        first_audio, pending_text = None, None
        try:
            while True:
                kind, payload = await queue.get()
                if kind == 'sentence':
                    spoken.append(payload)
                    pending_text = payload          # shown when its audio starts, not when the text is ready
                elif kind == 'pcm':
                    if first_audio is None:
                        first_audio = time.monotonic() - t0
                    if pending_text is not None:
                        await self.send_tts('sentence_start', pending_text)
                        pending_text = None
                    # the TTS was asked for exactly chunk_pause_ms of silence after the sentence, so
                    # nothing is held back: every block goes to the pacer as it arrives
                    tail = await self._send_pcm(tail + payload, pacer, gen)
                elif kind == 'chunk_end':
                    if pending_text is not None:    # a chunk that produced no audio still shows its text
                        await self.send_tts('sentence_start', pending_text)
                        pending_text = None
                elif kind == 'error':
                    self.log('reply failed: %s', payload, level=logging.ERROR)
                    if not spoken:
                        await self.send_tts('sentence_start', payload)
                elif kind == 'end':
                    break
            if tail:
                await self._send_pcm(tail + bytes((-len(tail)) % (self.out_frame * 2)), pacer, gen)
            await pacer.drain()
            self.log('spoke %d sentence(s), first audio %s, total %.1fs', len(spoken),
                     'none' if first_audio is None else f'{first_audio:.2f}s', time.monotonic() - t0)
        except asyncio.CancelledError:
            self.log('reply cancelled after %d sentence(s)', len(spoken))
            raise
        finally:
            cancel.set()
            self.dialogue.assistant(' '.join(spoken))
            self.guard_until = time.monotonic() + self.guard_s
            self.speaking = False
            self._reset_listen()
            self.last_voice_at = time.monotonic()
            if not self.closed:
                try:
                    await self.send_tts('stop')
                except Exception:
                    pass

    async def _send_pcm(self, data, pacer, gen=None):
        """Cut PCM into frames, encode and send on the pacer's schedule; return the leftover bytes."""
        size = self.out_frame * 2
        gen = self.gen if gen is None else gen
        if len(data) >= size:   # trace: frames handed to the pacer, before pacing
            self.ctx.bus.publish({'type': 'release', 'frames': len(data) // size, 'gen': gen})
        while len(data) >= size:
            frame, data = data[:size], data[size:]
            if self.agc is not None:
                frame = self.agc.process(np.frombuffer(frame, dtype=np.int16)).tobytes()
            gap = await pacer.slot()
            await self.ws.send(self.codec.encode(frame, self.out_frame))
            self.ctx.bus.publish({'type': 'frame', 'peak': int(np.abs(np.frombuffer(frame, dtype=np.int16)).max()),
                                  'gap': round(gap, 3), 'gen': gen})
        return data

    def _produce(self, dialogue, queue, cancel, loop, gen):
        """Worker thread: LLM pieces -> sentences -> TTS PCM, all pushed to the asyncio queue.

        Each sentence is synthesized to completion before the LLM gets the next turn, so the
        two never overlap on the GPU here regardless of the provider's own hand-off polling."""
        def put(kind, payload=None):
            try:
                loop.call_soon_threadsafe(queue.put_nowait, (kind, payload))
            except RuntimeError:   # loop already closed (the connection is gone); nobody is listening
                cancel.set()

        splitter = SentenceSplitter(**self.cfg['splitter'])

        def say(sentence):
            # trace events are published here, when things happen, not when the event loop (paced by
            # the frames it sends) gets around to the queue: tts_start = request sent, pcm = block arrived
            put('sentence', sentence)
            self.ctx.bus.publish({'type': 'tts_start', 'gen': gen})
            stream = self.ctx.tts.stream(sentence, pause=self.pause_s)
            try:
                for chunk in stream:
                    if cancel.is_set():
                        break
                    self.ctx.bus.publish({'type': 'pcm', 'ms': round(len(chunk) / 2 / self.out_rate * 1000, 1), 'gen': gen})
                    put('pcm', chunk)
            finally:
                stream.close()
            self.ctx.bus.publish({'type': 'chunk_end', 'gen': gen})
            put('chunk_end')

        def sentences():
            reply = self.ctx.brain.reply(dialogue)
            try:
                for piece in reply:
                    if cancel.is_set():
                        return
                    for sentence in splitter.feed(piece):
                        if cancel.is_set():
                            return
                        yield sentence
            finally:
                reply.close()
            self.ctx.bus.publish({'type': 'llm_done', 'gen': gen})
            if rest := splitter.flush():
                yield rest

        try:
            if self.cfg['llm'].get('overlap'):
                # demo switch: let the LLM run ahead on the GPU while TTS speaks (what gpu-duet avoids)
                pending, errors = queue_module.Queue(), []

                def ahead():
                    try:
                        for sentence in sentences():
                            pending.put(sentence)
                    except Exception as error:
                        errors.append(error)
                    pending.put(None)
                threading.Thread(target=ahead, daemon=True).start()
                while (sentence := pending.get()) is not None:
                    if cancel.is_set():
                        break
                    say(sentence)
                if errors:
                    raise errors[0]
            else:
                for sentence in sentences():
                    say(sentence)
        except Exception as error:
            put('error', f'{type(error).__name__}: {error}')
        finally:
            put('end')

    async def play_wake_ack(self):
        """Pre-encoded acknowledgement, played like a one-sentence reply."""
        frames, text = self.ctx.wake_ack
        self.last_voice_at = time.monotonic()
        if not frames:
            return
        self.speaking = True
        pacer = Pacer(self.FRAME_MS / 1000)
        try:
            await self.send_tts('start')
            await self.send_tts('sentence_start', text)
            for packet in frames:
                await pacer.slot()
                await self.ws.send(packet)
            await pacer.drain()
        finally:
            self.guard_until = time.monotonic() + self.guard_s
            self.speaking = False
            self._reset_listen()
            await self.send_tts('stop')

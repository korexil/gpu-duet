"""Talk to the standalone server without a device: send a WAV as a xiaozhi client, save the reply.

  python standalone/loopback.py --wav question.wav --out reply.wav
  python standalone/loopback.py --text "今天天气怎么样"      # skip the microphone side
  python standalone/loopback.py --text "讲个故事" --abort-after 2 --then "现在几点"
                                                            # press the button 2 s into the answer, ask again

Speaks the same protocol as the firmware (hello, Opus frames at 16 kHz / 60 ms, tts start/stop,
abort), so a working loopback means a device with the same OTA target will work too.
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent))
from opus_codec import OpusCodec  # noqa: E402

FRAME_MS = 60


def read_wav_16k(path):
    data, rate = sf.read(path, dtype='float32', always_2d=True)
    mono = data.mean(axis=1)
    if rate != 16000:
        mono = np.interp(np.arange(0, len(mono), rate / 16000), np.arange(len(mono)), mono)
    return (np.clip(mono, -1, 1) * 32767).astype(np.int16)


async def collect_answer(ws, codec, out_frame, t_sent, abort_after=None):
    """Receive one answer up to its ``tts stop``. With ``abort_after``, send an abort (what the
    board's button sends) that many seconds after the first audio frame and keep receiving until
    the stop. Returns timings relative to ``t_sent`` plus the decoded audio."""
    audio, first_audio, aborted_at, frames_after_abort = [], None, None, 0
    while True:
        msg = await asyncio.wait_for(ws.recv(), timeout=120)
        now = time.monotonic()
        if isinstance(msg, bytes):
            if first_audio is None:
                first_audio = now - t_sent
            audio.append(codec.decode(msg, out_frame))
            if aborted_at is not None:
                frames_after_abort += 1
            elif abort_after is not None and now - (t_sent + first_audio) >= abort_after:
                await ws.send(json.dumps({'type': 'abort', 'reason': 'wake_word_detected'}))
                aborted_at = now - t_sent
                print(f'  abort sent (+{aborted_at:.2f}s, wall {time.time():.3f})')
            continue
        data = json.loads(msg)
        if data.get('type') == 'stt':
            print(f'heard: {data.get("text")}  (+{now - t_sent:.2f}s)')
        elif data.get('type') == 'tts' and data.get('state') == 'sentence_start':
            print(f'  say: {data.get("text")}  (+{now - t_sent:.2f}s)')
        elif data.get('type') == 'tts' and data.get('state') == 'stop':
            break
    pcm = np.frombuffer(b''.join(audio), dtype=np.int16)
    result = dict(first_audio=first_audio, stop=time.monotonic() - t_sent, audio=pcm,
                  aborted_at=aborted_at, frames_after_abort=frames_after_abort)
    first = 'none' if first_audio is None else f'{first_audio:.2f}s'
    print(f'reply: {len(pcm) / 24000:.1f}s of audio, first audio after {first}, stop after {result["stop"]:.2f}s'
          + (f', {frames_after_abort} frame(s) after the abort' if aborted_at is not None else ''))
    return result


async def run(url, wav, text, out, trailing_silence_s=1.0, abort_after=None, then=None, before_then=None):
    in_frame = 16000 * FRAME_MS // 1000
    async with websockets.connect(url, additional_headers={'Device-Id': 'loopback', 'Client-Id': 'loopback',
                                                           'Protocol-Version': '1'}, max_size=2 ** 20) as ws:
        await ws.send(json.dumps({'type': 'hello', 'version': 1, 'transport': 'websocket',
                                  'audio_params': {'format': 'opus', 'sample_rate': 16000, 'channels': 1,
                                                   'frame_duration': FRAME_MS}}))
        hello = json.loads(await ws.recv())
        out_rate = hello['audio_params']['sample_rate']
        codec = OpusCodec(in_rate=out_rate, out_rate=16000)   # mirror of the server: decode 24 k, encode 16 k
        out_frame = out_rate * FRAME_MS // 1000
        print('server hello:', hello)
        if text:
            await ws.send(json.dumps({'type': 'listen', 'state': 'detect', 'text': text}))
            t_sent = time.monotonic()
        else:
            pcm = read_wav_16k(wav)
            pcm = np.concatenate([pcm, np.zeros(int(16000 * trailing_silence_s), dtype=np.int16)])
            await ws.send(json.dumps({'type': 'listen', 'state': 'start', 'mode': 'auto'}))
            for i in range(0, len(pcm) - in_frame + 1, in_frame):
                await ws.send(codec.encode(pcm[i:i + in_frame].tobytes(), in_frame))
                await asyncio.sleep(FRAME_MS / 1000)
            t_sent = time.monotonic()
            print(f'sent {len(pcm) / 16000:.1f}s of audio')
        answers = [await collect_answer(ws, codec, out_frame, t_sent, abort_after)]
        if then:
            if before_then is not None:
                result = before_then()
                if asyncio.iscoroutine(result):
                    await result
            await ws.send(json.dumps({'type': 'listen', 'state': 'detect', 'text': then}))
            print(f'asked again: {then!r} (wall {time.time():.3f})')
            answers.append(await collect_answer(ws, codec, out_frame, time.monotonic()))
        if out:
            gap = np.zeros(out_rate // 2, dtype=np.int16)
            joined = np.concatenate([x for a in answers for x in (a['audio'], gap)][:-1])
            sf.write(out, joined, out_rate, subtype='PCM_16')
            print('saved', out)
        return answers


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='ws://127.0.0.1:8000/xiaozhi/v1/')
    parser.add_argument('--wav', help='16-bit WAV with the question (any rate)')
    parser.add_argument('--text', help='send this as device text instead of audio')
    parser.add_argument('--out', help='save the reply audio here (answers joined with 0.5 s of silence)')
    parser.add_argument('--abort-after', type=float, help='send an abort this many seconds after the first audio')
    parser.add_argument('--then', help='after the first answer (or its abort), ask this as device text')
    args = parser.parse_args()
    if not args.wav and not args.text:
        parser.error('--wav or --text')
    asyncio.run(run(args.url, args.wav, args.text, args.out, abort_after=args.abort_after, then=args.then))


if __name__ == '__main__':
    main()

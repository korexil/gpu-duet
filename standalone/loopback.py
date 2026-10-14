"""Talk to the standalone server without a device: send a WAV as a xiaozhi client, save the reply.

  python standalone/loopback.py --wav question.wav --out reply.wav
  python standalone/loopback.py --text "今天天气怎么样"      # skip the microphone side

Speaks the same protocol as the firmware (hello, Opus frames at 16 kHz / 60 ms, tts start/stop),
so a working loopback means a device with the same OTA target will work too.
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


async def run(url, wav, text, out, trailing_silence_s=1.0):
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
        t_sent = None
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
        audio, first_audio = [], None
        while True:
            msg = await asyncio.wait_for(ws.recv(), timeout=120)
            if isinstance(msg, bytes):
                if first_audio is None:
                    first_audio = time.monotonic() - t_sent
                audio.append(codec.decode(msg, out_frame))
                continue
            data = json.loads(msg)
            if data.get('type') == 'stt':
                print(f'heard: {data.get("text")}  (+{time.monotonic() - t_sent:.2f}s)')
            elif data.get('type') == 'tts' and data.get('state') == 'sentence_start':
                print(f'  say: {data.get("text")}  (+{time.monotonic() - t_sent:.2f}s)')
            elif data.get('type') == 'tts' and data.get('state') == 'stop':
                break
        pcm_out = np.frombuffer(b''.join(audio), dtype=np.int16)
        print(f'reply: {len(pcm_out) / out_rate:.1f}s of audio, first audio after {first_audio:.2f}s')
        if out:
            sf.write(out, pcm_out, out_rate, subtype='PCM_16')
            print('saved', out)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='ws://127.0.0.1:8000/xiaozhi/v1/')
    parser.add_argument('--wav', help='16-bit WAV with the question (any rate)')
    parser.add_argument('--text', help='send this as device text instead of audio')
    parser.add_argument('--out', help='save the reply audio here')
    args = parser.parse_args()
    if not args.wav and not args.text:
        parser.error('--wav or --text')
    asyncio.run(run(args.url, args.wav, args.text, args.out))


if __name__ == '__main__':
    main()

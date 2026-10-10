"""Talk to xiaozhi-esp32-server as a software device and measure what a board would hear.

For every turn: time to first Opus packet, decoded audio length, the largest gap
between packets and the playback underrun of an ideal 60 ms-per-packet player.
Decoded audio is saved per turn, so loudness and pauses can be checked with
loudness_stats.py.

  pip install websockets opuslib-next numpy soundfile
  python device_probe.py --url ws://127.0.0.1:8000/xiaozhi/v1/ --auth-key <server.auth_key> \
      --turn "<name>, how are you?" --turn "tell me about your day" --out probe-out
  python device_probe.py ... --wav utterance_24k.wav          # one turn through VAD + ASR
"""
import argparse
import asyncio
import base64
import hmac
import json
import time
from hashlib import sha256
from pathlib import Path

import numpy as np
import opuslib_next
import soundfile as sf
import websockets

FRAME = 1440  # 60 ms at 24 kHz


def auth_header(key, client_id, device_id):
    stamp = int(time.time())
    digest = hmac.new(key.encode(), f"{client_id}|{device_id}|{stamp}".encode(), sha256).digest()
    return f"Bearer {base64.urlsafe_b64encode(digest).decode().rstrip('=')}.{stamp}"


async def connect(args):
    headers = {"Device-Id": args.device_id, "Client-Id": args.client_id}
    if args.auth_key:
        headers["Authorization"] = auth_header(args.auth_key, args.client_id, args.device_id)
    ws = await websockets.connect(args.url, additional_headers=headers, max_size=None, open_timeout=10)
    await ws.send(json.dumps(dict(type="hello", version=1, transport="websocket",
                                  audio_params=dict(format="opus", sample_rate=24000, channels=1, frame_duration=60))))
    hello = json.loads(await asyncio.wait_for(ws.recv(), 10))
    if hello.get("type") != "hello":
        raise RuntimeError(f"unexpected hello: {hello}")
    await asyncio.sleep(2)
    return ws


async def receive(ws, label, start, out_dir, timeout):
    decoder = opuslib_next.Decoder(24000, 1)
    arrivals, pcm, sentences = [], [], []
    while True:
        message = await asyncio.wait_for(ws.recv(), max(0.01, timeout - (time.monotonic() - start)))
        if isinstance(message, bytes):
            pcm.append(decoder.decode(message, FRAME))
            arrivals.append(time.monotonic() - start)
            continue
        item = json.loads(message)
        if item.get("type") == "tts" and item.get("state") == "sentence_start" and item.get("text"):
            sentences.append(item["text"])
        if item.get("type") == "tts" and item.get("state") == "stop":
            break
    if not arrivals:
        return dict(label=label, first_opus_seconds=None, sentences=sentences)
    wave = np.frombuffer(b"".join(pcm), dtype="<i2")
    sf.write(out_dir / f"{label}.wav", wave, 24000, subtype="PCM_16")
    deficit = max(max(0.0, t - arrivals[0] - i * 0.06) for i, t in enumerate(arrivals))
    return dict(label=label, first_opus_seconds=round(arrivals[0], 3), audio_seconds=round(len(wave) / 24000, 2),
                max_gap_seconds=round(max((b - a for a, b in zip(arrivals, arrivals[1:])), default=0), 3),
                playback_deficit_seconds=round(deficit, 3), sentences=sentences)


def encode_wav(path):
    audio, rate = sf.read(path, dtype="float32")
    if rate != 24000 or audio.ndim != 1:
        raise ValueError("expected a 24 kHz mono WAV")
    samples = np.clip(audio * 32767, -32768, 32767).astype("<i2")
    encoder = opuslib_next.Encoder(24000, 1, opuslib_next.APPLICATION_AUDIO)
    frames = [np.pad(samples[i:i + FRAME], (0, max(0, FRAME - len(samples[i:i + FRAME])))) for i in range(0, len(samples), FRAME)]
    silence = [np.zeros(FRAME, dtype="<i2")] * 16  # let the server VAD see the end of speech
    return [encoder.encode(f.tobytes(), FRAME) for f in frames + silence]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="ws://127.0.0.1:8000/xiaozhi/v1/")
    parser.add_argument("--auth-key", help="server.auth_key, if device auth is enabled")
    parser.add_argument("--device-id", default="02:00:00:00:00:01")
    parser.add_argument("--client-id", default="device-probe")
    parser.add_argument("--turn", action="append", default=[], help="text turn, repeatable")
    parser.add_argument("--wav", help="24 kHz mono utterance sent as audio (exercises VAD + ASR)")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--out", default="probe-out")
    args = parser.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(exist_ok=True)
    rows = []
    ws = await connect(args)
    try:
        for index, text in enumerate(args.turn):
            start = time.monotonic()
            await ws.send(json.dumps(dict(type="listen", state="detect", text=text), ensure_ascii=False))
            row = await receive(ws, f"turn{index}", start, out_dir, args.timeout)
            row["input"] = text
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            await asyncio.sleep(1.5)
        if args.wav:
            await ws.send(json.dumps(dict(type="listen", state="start", mode="auto")))
            packets = encode_wav(args.wav)
            for packet in packets[:-16]:
                await ws.send(packet)
                await asyncio.sleep(0.06)
            start = time.monotonic()  # measured from the end of the uploaded speech
            task = asyncio.create_task(receive(ws, "wav", start, out_dir, args.timeout))
            for packet in packets[-16:]:
                await ws.send(packet)
                await asyncio.sleep(0.06)
            row = await task
            row["input"] = args.wav
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        await ws.close()
    (out_dir / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())

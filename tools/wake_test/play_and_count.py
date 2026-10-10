"""Play clips from the PC speaker to an ESP32 board and count on-device wake detections.

Reads the board's serial log (xiaozhi CustomWakeWord prints "wake word detected ...
prob=..."). Stop the xiaozhi server while testing: a woken board that cannot connect
falls straight back to idle, so clips can be played back to back.

  pip install pyserial
  python play_and_count.py --port COM5 --samples samples/samples.json --tag baseline
"""
import argparse
import json
import re
import threading
import time
import winsound

import serial


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', required=True)
    parser.add_argument('--samples', required=True)
    parser.add_argument('--tag', required=True)
    args = parser.parse_args()
    rows = json.load(open(args.samples, encoding='utf-8'))
    port = serial.Serial()
    port.port, port.baudrate, port.timeout = args.port, 115200, 0.1
    port.dtr = port.rts = False  # opening must not reset the board
    port.open()
    lines, lock, stop = [], threading.Lock(), False

    def reader():
        buffer = b''
        while not stop:
            buffer += port.read(4096)
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n', 1)
                with lock:
                    lines.append((time.time(), line.decode('utf-8', 'replace').strip()))

    threading.Thread(target=reader, daemon=True).start()

    def state():
        with lock:
            for _, line in reversed(lines):
                match = re.search(r'State: \S+ -> (\S+)', line)
                if match:
                    return match.group(1)
        return None

    results = []
    time.sleep(1)
    for row in rows:
        deadline = time.time() + 25
        while state() not in (None, 'idle') and time.time() < deadline:
            time.sleep(0.2)
        start = time.time()
        winsound.PlaySound(row['file'], winsound.SND_FILENAME)
        time.sleep(2.0)
        with lock:
            hits = [line for t, line in lines if t >= start and 'wake word detected' in line.lower()]
        prob = re.search(r'prob=([\d.]+)', hits[0]).group(1) if hits else None
        results.append(dict(row, detected=bool(hits), prob=prob))
        print(f"{row['label']} {'HIT ' if hits else 'miss'} {prob or '':8} {row['text']}", flush=True)
    stop = True
    port.close()
    positives = [r for r in results if r['label'] == 'pos']
    negatives = [r for r in results if r['label'] == 'neg']
    print(f"== {args.tag}: recall {sum(r['detected'] for r in positives)}/{len(positives)}  "
          f"false wakes {sum(r['detected'] for r in negatives)}/{len(negatives)}")
    json.dump(results, open(f'result-{args.tag}.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()

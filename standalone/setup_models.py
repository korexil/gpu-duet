"""Download the CPU models the standalone server needs: SenseVoice (int8, ~240 MB), Silero VAD (~2 MB)
and the GTCRN denoiser (~0.5 MB).

  python standalone/setup_models.py            # into standalone/models/
  python standalone/setup_models.py --out D:\models\asr
"""
import argparse
import sys
import urllib.request
from pathlib import Path

HF = 'https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main/'
FILES = [
    ('sense-voice/model.int8.onnx', [HF + 'model.int8.onnx', HF.replace('huggingface.co', 'hf-mirror.com') + 'model.int8.onnx']),
    ('sense-voice/tokens.txt', [HF + 'tokens.txt', HF.replace('huggingface.co', 'hf-mirror.com') + 'tokens.txt']),
    # v5 first (input/state/sr); the v4 file sherpa-onnx ships (x/h/c) also works
    ('gtcrn_simple.onnx', ['https://github.com/k2-fsa/sherpa-onnx/releases/download/speech-enhancement-models/gtcrn_simple.onnx']),
    ('silero_vad.onnx', ['https://github.com/snakers4/silero-vad/raw/v5.1.2/src/silero_vad/data/silero_vad.onnx',
                         'https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx']),
]


def fetch(url, target):
    tmp = target.with_suffix(target.suffix + '.part')
    with urllib.request.urlopen(url, timeout=60) as response, tmp.open('wb') as out:
        total = int(response.headers.get('Content-Length') or 0)
        done = 0
        while chunk := response.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if total:
                print(f'\r  {done / 1e6:.0f}/{total / 1e6:.0f} MB', end='', flush=True)
    print()
    tmp.replace(target)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default=str(Path(__file__).resolve().parent / 'models'))
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    out = Path(args.out)
    for relative, urls in FILES:
        target = out / relative
        if target.exists() and not args.force:
            print(f'{target}: exists')
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        for url in urls:
            print(f'{relative} <- {url}')
            try:
                fetch(url, target)
                break
            except Exception as error:
                print(f'  failed: {error}')
        else:
            print(f'could not download {relative}', file=sys.stderr)
            return 1
    print(f'models in {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

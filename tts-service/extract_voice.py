"""Extract the voice-clone prompt from your reference recording.

  python extract_voice.py --library <dir>/qwen3_tts_bridge.dll --models <tts models> \
      --wav my_voice.wav --text my_voice.txt --out voice/icl-prompt.json

Runs qwen3-tts-cli.exe from the same folder as the library on the CPU, with the same
DLL search dirs as server.py (the library's folder, its bin/, the nvidia-* wheels).
"""
import argparse
import os
import subprocess
from pathlib import Path

from server import default_dll_dirs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--library', required=True, help='qwen3_tts_bridge shared library')
    parser.add_argument('--models', required=True, help='directory with the converted GGUF models')
    parser.add_argument('--wav', required=True, help='3-10 s of clean speech')
    parser.add_argument('--text', required=True, help='UTF-8 file with the exact transcript of --wav')
    parser.add_argument('--out', required=True, help='where to write the prompt JSON')
    parser.add_argument('--dll-dir', action='append', default=[])
    args = parser.parse_args()

    folder = Path(args.library).resolve().parent
    cli = next((p for p in (folder / 'qwen3-tts-cli.exe', folder / 'qwen3-tts-cli') if p.exists()), None)
    if cli is None:
        raise SystemExit(f'qwen3-tts-cli not found next to {args.library}')
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env['PATH'] = os.pathsep.join(default_dll_dirs(args.library) + args.dll_dir + [env.get('PATH', '')])
    subprocess.run([str(cli), '-m', args.models, '--backend', 'cpu', '-j', '4', '-r', args.wav,
                    '--reference-text-file', args.text, '--extract-icl-prompt', args.out], env=env, check=True)
    print(f'wrote {args.out}')


if __name__ == '__main__':
    main()

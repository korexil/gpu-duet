"""2x2 benchmark: GPU turn-taking on/off x long/short sentences.

The sentence pause lives in tts-service, so the two sentence cases need a TTS
restart in between:

  # 1. tts-service started with --sentence-pause 0
  python bench/run.py --sentences long
  # 2. tts-service restarted with --sentence-pause 0.9
  python bench/run.py --sentences short
  # 3. table + CSV from everything recorded so far
  python bench/run.py --summary

Every (question, seed) pair runs with turn-taking off ('plain') and on ('turns').
With the same seed both modes generate the same text, so the pair differs only in
scheduling. The order alternates, and each run waits --idle seconds first so the
GPU starts from idle clocks (the hardest case for the first sentence).
"""
import argparse
import csv
import hashlib
import importlib.util
import json
import platform
import statistics
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / 'bench' / 'results'
BASE_SYSTEM = ('あなたは親しみやすい話し相手です。声で話すので、絵文字や箇条書きは使わず、'
               '自然な話し言葉で話してください。')
SYSTEMS = {
    'long': BASE_SYSTEM,
    'short': BASE_SYSTEM + '一文には一つのことだけ。一文は15字くらいまでの短い文にして、こまめに「。」で区切ってください。',
}
QUESTIONS = [
    '好きな季節について、少し長めに話して。',
    '最近おもしろいと思ったことを、詳しく教えて。',
    '理想の休日の過ごし方を、長めに話して。',
]
SEEDS = [11, 22]
CASES = {
    ('long', 'plain'): '无调度',
    ('long', 'turns'): '仅 GPU 协调',
    ('short', 'plain'): '仅缩短句子',
    ('short', 'turns'): '缩短句子 + GPU 协调',
}


def get_json(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.load(response)


def environment(llm, tts):
    gpu = subprocess.run(['nvidia-smi', '--query-gpu=name,driver_version,memory.total', '--format=csv,noheader'],
                         capture_output=True, text=True).stdout.strip()
    props = get_json(llm + '/props')
    model = Path(props.get('model_path', '')).name
    health = get_json(tts + '/health')
    return dict(gpu=gpu, os=platform.platform(), python=platform.python_version(), llm_model=model,
                llm_ctx=props.get('default_generation_settings', {}).get('n_ctx'),
                tts_backend=health.get('active_backend'), sentence_pause=health.get('sentence_pause'))


def reset_llm_cache(llm):
    """Displace the slot's prompt cache. A cached prefix of a different length changes the
    batch shapes, the logits drift slightly and the same seed no longer gives the same text."""
    request = urllib.request.Request(llm + '/completion', headers={'Content-Type': 'application/json'},
                                     data=json.dumps({'prompt': 'x', 'n_predict': 1, 'cache_prompt': False}).encode())
    urllib.request.urlopen(request, timeout=30).read()


def load_sim():
    spec = importlib.util.spec_from_file_location('turn_taking_sim', ROOT / 'tools' / 'turn_taking_sim.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def record(args):
    sim = load_sim()
    RESULTS.mkdir(parents=True, exist_ok=True)
    env = environment(args.llm, args.tts)
    expected = 0 if args.sentences == 'long' else 0.9
    if env['sentence_pause'] != expected:
        raise SystemExit(f"tts-service reports sentence_pause={env['sentence_pause']}, "
                         f"restart it with --sentence-pause {expected} for --sentences {args.sentences}")
    (RESULTS / f'env-{args.sentences}.json').write_text(json.dumps(env, ensure_ascii=False, indent=2) + '\n',
                                                         encoding='utf-8')
    pairs = [(q, seed) for q in range(len(QUESTIONS)) for seed in SEEDS][:args.limit or None]
    with (RESULTS / 'runs.jsonl').open('a', encoding='utf-8') as out:
        for index, (question, seed) in enumerate(pairs):
            for mode in (('plain', 'turns') if index % 2 == 0 else ('turns', 'plain')):
                reset_llm_cache(args.llm)
                time.sleep(args.idle)
                result = sim.run(mode, QUESTIONS[question], SYSTEMS[args.sentences], args.llm, args.tts,
                                 seed=seed)
                text = result.pop('text')
                row = dict(case=args.sentences, sentence_pause=env['sentence_pause'], question=question,
                           seed=seed, text_sha=hashlib.sha256(text.encode()).hexdigest()[:12], **result,
                           text=text)
                out.write(json.dumps(row, ensure_ascii=False) + '\n')
                out.flush()
                print(CASES[(args.sentences, mode)], {k: row[k] for k in
                      ('question', 'seed', 'first_audio_s', 'underrun_total_s', 'worst_gap_s', 'sentences')},
                      flush=True)


def summary():
    rows = [json.loads(line) for line in (RESULTS / 'runs.jsonl').open(encoding='utf-8')]
    texts = {}
    for row in rows:
        texts.setdefault((row['case'], row['question'], row['seed']), set()).add(row['text_sha'])
    same = sum(len(v) == 1 for v in texts.values())
    span = lambda xs, f='{:.2f}': f'{f.format(statistics.median(xs))}（{f.format(min(xs))}～{f.format(max(xs))}）'
    table = []
    for (sentences, mode), label in CASES.items():
        group = [r for r in rows if r['case'] == sentences and r['mode'] == mode]
        if not group:
            continue
        table.append(dict(
            case=label, sentences=sentences, mode=mode, runs=len(group),
            sentence_pause_s=group[0]['sentence_pause'],
            sentence_chars_median=statistics.median(r['sentence_chars_median'] for r in group),
            first_audio_s=span([r['first_audio_s'] for r in group]),
            underrun_total_s=span([r['underrun_total_s'] for r in group]),
            worst_gap_s=span([r['worst_gap_s'] for r in group]),
            runs_with_underrun=f"{sum(r['underrun_total_s'] > 0 for r in group)}/{len(group)}",
            audio_s=span([r['audio_s'] for r in group], '{:.0f}'),
        ))
    with (RESULTS / 'summary.csv').open('w', encoding='utf-8', newline='') as out:
        writer = csv.DictWriter(out, fieldnames=list(table[0]))
        writer.writeheader()
        writer.writerows(table)
    print('| 情况 | 次数 | 句长中位数（字） | 首声 s | 断音合计 s | 最长断档 s | 有断音的次数 |')
    print('|---|---|---|---|---|---|---|')
    for r in table:
        print(f"| {r['case']} | {r['runs']} | {r['sentence_chars_median']:g} | {r['first_audio_s']} | "
              f"{r['underrun_total_s']} | {r['worst_gap_s']} | {r['runs_with_underrun']} |")
    print(f'\n同一 (问题, 种子) 下两种模式生成的文字相同：{same}/{len(texts)}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sentences', choices=['long', 'short'])
    parser.add_argument('--summary', action='store_true')
    parser.add_argument('--llm', default='http://127.0.0.1:18080')
    parser.add_argument('--tts', default='http://127.0.0.1:18766')
    parser.add_argument('--idle', type=float, default=10, help='seconds before each run (GPU back to idle clocks)')
    parser.add_argument('--limit', type=int, help='only the first N (question, seed) pairs')
    args = parser.parse_args()
    if args.summary:
        summary()
    elif args.sentences:
        record(args)
    else:
        parser.error('pass --sentences long|short or --summary')


if __name__ == '__main__':
    main()

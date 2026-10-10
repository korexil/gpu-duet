"""Generate wake-word test clips with a local VOICEVOX engine (Japanese voices).

  python gen_samples.py --word "<name>" --negatives negatives.txt --out samples

Positives: N speakers x {name, name！, ねえ、name} x speed {1.0, 1.25}.
Negatives: one line per phrase (include near-miss words that sound like the name).
"""
import argparse
import json
import os
import random
import urllib.parse
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--word', required=True)
    parser.add_argument('--negatives', required=True)
    parser.add_argument('--engine', default='http://127.0.0.1:50021')
    parser.add_argument('--speakers', type=int, default=10)
    parser.add_argument('--out', default='samples')
    args = parser.parse_args()
    engine = args.engine
    speakers = json.load(urllib.request.urlopen(engine + '/speakers'))
    styles = [st['id'] for s in speakers for st in s['styles'] if st['name'] in ('ノーマル', 'ふつう')]
    random.seed(3)
    random.shuffle(styles)
    styles = styles[:args.speakers]
    os.makedirs(args.out, exist_ok=True)

    def synth(text, speaker, speed, path):
        query = json.load(urllib.request.urlopen(urllib.request.Request(
            engine + '/audio_query?' + urllib.parse.urlencode({'text': text, 'speaker': speaker}), method='POST')))
        query.update(speedScale=speed, prePhonemeLength=0.1, postPhonemeLength=0.2, outputSamplingRate=24000)
        wav = urllib.request.urlopen(urllib.request.Request(engine + f'/synthesis?speaker={speaker}',
                                     data=json.dumps(query).encode(), headers={'Content-Type': 'application/json'})).read()
        open(path, 'wb').write(wav)

    rows = []
    for i, speaker in enumerate(styles):
        for j, text in enumerate([args.word, args.word + '！', 'ねえ、' + args.word]):
            for speed in (1.0, 1.25):
                path = os.path.join(args.out, f'p_{i}_{j}_{speed}.wav')
                synth(text, speaker, speed, path)
                rows.append(dict(file=path, label='pos', text=text, speaker=speaker, speed=speed))
    negatives = [line.strip() for line in open(args.negatives, encoding='utf-8') if line.strip()]
    for k, text in enumerate(negatives):
        path = os.path.join(args.out, f'n_{k}.wav')
        synth(text, styles[k % len(styles)], 1.1, path)
        rows.append(dict(file=path, label='neg', text=text, speaker=styles[k % len(styles)], speed=1.1))
    json.dump(rows, open(os.path.join(args.out, 'samples.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(len(rows), 'clips')


if __name__ == '__main__':
    main()

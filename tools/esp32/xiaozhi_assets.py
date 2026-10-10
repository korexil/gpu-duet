"""Inspect and rebuild the xiaozhi-esp32 "assets" partition (mmap assets + ESP-SR models).

The firmware reads its custom wake word setup (MultiNet model, threshold, commands)
from index.json inside this partition, so wake-word experiments only need the assets
partition re-flashed, not the firmware.

  python xiaozhi_assets.py unpack assets.bin out/
  python xiaozhi_assets.py multinet out/ --threshold 0.06 --commands commands.txt --text "<display name>" -o new.bin
  python xiaozhi_assets.py srmodels --esp-sr <managed_components/espressif__esp-sr> --models mn5q8_cn -o out/srmodels.bin

Read the partition from a board / write it back with esptool, e.g.
  python -m esptool --chip esp32s3 -p COM5 read-flash <offset> <size> assets.bin
  python -m esptool --chip esp32s3 -p COM5 write-flash <offset> new.bin
(take offset and size of the "assets" entry from your partition table).
"""
import argparse
import json
import os
import struct


def unpack(path, out_dir):
    data = open(path, 'rb').read()
    count, checksum, length = struct.unpack('<III', data[:12])
    body = data[12:12 + length]
    if sum(body) & 0xFFFF != checksum:
        raise ValueError('checksum mismatch')
    table, blob = body[:count * 44], body[count * 44:]
    os.makedirs(out_dir, exist_ok=True)
    for i in range(count):
        entry = table[i * 44:(i + 1) * 44]
        name = entry[:32].rstrip(b'\0').decode()
        size, offset = struct.unpack('<II', entry[32:40])
        if blob[offset:offset + 2] != b'\x5a\x5a':
            raise ValueError(f'bad marker for {name}')
        open(os.path.join(out_dir, name), 'wb').write(blob[offset + 2:offset + 2 + size])
        print(f'{name:32} {size}')


def pack(files, out):
    """files: ordered (name, bytes); same layout as scripts/build_default_assets.py."""
    merged, table = bytearray(), bytearray()
    for name, blob in files:
        table += name.encode().ljust(32, b'\0')[:32] + struct.pack('<IIHH', len(blob), len(merged), 0, 0)
        merged += b'\x5a\x5a' + blob
    body = table + merged
    open(out, 'wb').write(struct.pack('<III', len(files), sum(body) & 0xFFFF, len(body)) + body)
    return 12 + len(body)


def pack_srmodels(esp_sr, models, out):
    """Pack esp-sr model dirs into srmodels.bin (layout of build_default_assets.pack_models)."""
    root = os.path.join(esp_sr, 'model', 'multinet_model')
    if any(m.startswith(('mn6', 'mn7')) for m in models) and 'fst' not in models:
        models = models + ['fst']  # MultiNet 6/7 need the fst directory
    tree = {m: {f: open(os.path.join(root, m, f), 'rb').read() for f in sorted(os.listdir(os.path.join(root, m)))}
            for m in models}
    header_len = 4 + len(tree) * 36 + sum(len(v) for v in tree.values()) * 40
    head, data = struct.pack('<I', len(tree)), b''
    for name, files in tree.items():
        head += name.encode().ljust(32, b'\0') + struct.pack('<I', len(files))
        for fname, blob in files.items():
            head += fname.encode().ljust(32, b'\0') + struct.pack('<II', header_len + len(data), len(blob))
            data += blob
    open(out, 'wb').write(head + data)
    return len(head) + len(data)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('unpack'); p.add_argument('assets'); p.add_argument('out_dir')
    p = sub.add_parser('multinet'); p.add_argument('dir'); p.add_argument('--threshold', type=float, required=True)
    p.add_argument('--commands', required=True, help='one pinyin/phoneme command per line')
    p.add_argument('--text', required=True); p.add_argument('--language', default='cn'); p.add_argument('-o', required=True)
    p = sub.add_parser('srmodels'); p.add_argument('--esp-sr', required=True); p.add_argument('--models', required=True)
    p.add_argument('-o', required=True)
    args = parser.parse_args()
    if args.cmd == 'unpack':
        unpack(args.assets, args.out_dir)
    elif args.cmd == 'srmodels':
        print(args.o, pack_srmodels(args.esp_sr, args.models.split(','), args.o))
    else:
        commands = [c.strip() for c in open(args.commands, encoding='utf-8') if c.strip()]
        index = {'version': 1, 'srmodels': 'srmodels.bin', 'multinet_model': {
            'language': args.language, 'duration': 3000, 'threshold': args.threshold,
            'commands': [{'command': c, 'text': args.text, 'action': 'wake'} for c in commands]}}
        files = [('srmodels.bin', open(os.path.join(args.dir, 'srmodels.bin'), 'rb').read()),
                 ('index.json', json.dumps(index, ensure_ascii=False, indent=2).encode())]
        print(args.o, pack(files, args.o), 'bytes,', len(commands), 'commands')


if __name__ == '__main__':
    main()

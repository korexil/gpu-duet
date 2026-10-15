"""Regenerate the README figures (light + dark SVG). No dependencies.

  python docs/img/make_figures.py

Numbers come from bench/results/runs.jsonl (medians, long sentences), docs/pitfalls.md,
docs/architecture.md and STAGES below (the four-stage run of 2026-10-14). The timeline is a
schematic; only its annotated values are measured. GPU memory is in Task Manager GB
(1024 MiB), measured 2026-10-14 (docs/benchmarks.md#复现): TTS 4,027-4,065 MiB, LLM 2,899 MiB,
total 7,448 MiB; "unoptimized" adds back the patch savings, 488 + 594 MiB.
"""
from pathlib import Path

OUT = Path(__file__).resolve().parent
FONT = "-apple-system,'Segoe UI','PingFang SC','Hiragino Sans GB','Microsoft YaHei','Noto Sans CJK SC',sans-serif"
THEMES = {
    "light": dict(text="#1f2328", muted="#59636e", grid="#d1d9e0", llm="#2a78d6", tts="#eb6834",
                  play="#1baf7a", neutral="#b7b5ae", bad="#e34948"),
    "dark": dict(text="#f0f6fc", muted="#9198a1", grid="#3d444d", llm="#3987e5", tts="#d95926",
                 play="#199e70", neutral="#5d5c58", bad="#e66767"),
}


def text_width(s, size):
    return sum(size if ord(ch) > 0x2E7F else size * 0.56 for ch in s)


def t(x, y, s, size=13, fill="text", anchor="start", weight=400):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{{{fill}}}" '
            f'text-anchor="{anchor}" font-weight="{weight}">{s}</text>')


def bar(x0, x1, y, h, fill, r=3):
    w = max(0.0, x1 - x0)
    return f'<rect x="{x0:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h}" rx="{min(r, w / 2):.1f}" fill="{{{fill}}}"/>'


def write(name, width, height, body):
    for theme, colors in THEMES.items():
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" '
               f'height="{height}" font-family="{FONT}">\n'
               '<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" '
               'patternTransform="rotate(45)"><rect width="6" height="6" fill="{bad}" fill-opacity="0.18"/>'
               '<line x1="0" y1="0" x2="0" y2="6" stroke="{bad}" stroke-width="2"/></pattern></defs>\n'
               + "\n".join(body) + "\n</svg>\n")
        (OUT / f"{name}-{theme}.svg").write_text(svg.format(**colors), encoding="utf-8", newline="\n")


def timeline():
    """Same three-sentence answer, concurrent vs turn-taking (schematic)."""
    x0, x1, seconds = 64, 470, 6.0
    sx = lambda s: x0 + (x1 - x0) * s / seconds
    lanes = [("LLM", "llm"), ("TTS", "tts"), ("播放", "play")]
    panels = [
        ("同时跑：TTS 被拖慢（RTF 1.1～1.6）", 3.20, "断音合计 2.27 s", {
            "llm": [(0, 6.0)],
            "tts": [(0.3, 2.9), (2.95, 4.9), (4.95, 6.0)],
            "play": [(3.2, 4.1), (4.6, 5.2), (5.6, 6.0)],
            "gap": [(4.1, 4.6), (5.2, 5.6)],
        }),
        ("轮流跑：写完一句就让出 GPU（RTF 约 0.6）", 1.83, "断音 0", {
            "llm": [(0, 0.3), (1.3, 1.6), (2.5, 2.8)],
            "tts": [(0.3, 1.3), (1.6, 2.5), (2.8, 3.7)],
            "play": [(1.83, 3.2), (3.2, 4.6), (4.6, 6.0)],
            "gap": [],
        }),
    ]
    body, y = [], 20
    for title, first, verdict, segs in panels:
        body.append(t(0, y, title, 15, weight=600))
        y += 12
        for lane, (label, key) in enumerate(lanes):
            ly = y + lane * 28
            body.append(t(x0 - 10, ly + 16, label, 13, "muted", "end"))
            for a, b in segs[key]:
                body.append(bar(sx(a) + 1, sx(b) - 1, ly, 22, key))
            if key == "play":
                for a, b in segs["gap"]:
                    body.append(f'<rect x="{sx(a):.1f}" y="{ly}" width="{sx(b) - sx(a):.1f}" height="22" fill="url(#hatch)"/>')
        py = y + 2 * 28
        fx = sx(first)
        body.append(f'<line x1="{fx:.1f}" y1="{y - 4}" x2="{fx:.1f}" y2="{py + 26}" stroke="{{text}}" stroke-width="1.5" stroke-dasharray="3 3"/>')
        body.append(t(fx - 6, py + 42, f"首声 {first:.2f} s", 14, anchor="end", weight=600))
        body.append(t(x1, py + 42, verdict, 14, "text", "end", 600))
        y = py + 78
    for s in range(int(seconds) + 1):
        body.append(f'<line x1="{sx(s):.1f}" y1="{y - 24}" x2="{sx(s):.1f}" y2="{y - 19}" stroke="{{grid}}"/>')
        body.append(t(sx(s), y - 4, f"{s} s", 12, "muted", "middle"))
    body.append(f'<rect x="0" y="{y + 10}" width="16" height="12" fill="url(#hatch)"/>')
    body.append(t(22, y + 21, "断音 · 示意图，标注的数值为实测", 13, "muted"))
    write("timeline", 480, y + 28, body)


def vram():
    """GPU memory on the 8 GB card, with and without the TTS storage switches."""
    x0, x1, top = 0, 470, 8.6
    sx = lambda gb: x0 + (x1 - x0) * gb / top
    rows = [("TTS 未做存储优化（推算）", "8.3 GB · 超出", [("TTS", 5.0, "tts"), ("LLM", 2.8, "llm"), ("", 0.5, "neutral")]),
            ("本仓库配置", "7.3 GB", [("TTS", 4.0, "tts"), ("LLM", 2.8, "llm"), ("", 0.5, "neutral")])]
    body, cap = [], sx(8.0)
    body.append(f'<line x1="{cap:.1f}" y1="22" x2="{cap:.1f}" y2="160" stroke="{{bad}}" stroke-width="1.5" stroke-dasharray="4 3"/>')
    body.append(t(cap, 15, "显存 8 GB", 13, "text", "middle", 600))
    for i, (label, total, segs) in enumerate(rows):
        y = 34 + i * 68
        body.append(t(0, y + 14, label, 14))
        body.append(t(cap - 8, y + 14, total, 14, "text", "end", 600))
        y += 24
        acc = 0.0
        for name, gb, key in segs:
            a, b = sx(acc) + (1 if acc else 0), sx(acc + gb) - 1
            body.append(bar(a, b, y, 28, key))
            if name:
                body.append(t((a + b) / 2, y + 19, f"{name} {gb:.1f}", 13, "text", "middle", 600))
            acc += gb
    body.append(bar(0, 14, 172, 12, "neutral", 2))
    body.append(t(20, 183, "桌面等其他占用约 0.5 GB · 单位 GB", 13, "muted"))
    write("vram", 480, 192, body)


# Same question ("a ~300-character story"), same cloned voice, same standalone pipeline,
# two runs per stage, 2026-10-14. Gaps are estimated against the device's 0.3 s pre-buffer
# (standalone/session.py Pacer). (first audio s, gap count, gap total s) per run.
# The first "换引擎" run is dropped: its record mixed two answers (see docs/benchmarks.md).
STAGES = [
    ("起点", "PyTorch TTS · LLM 在 CPU", [(6.67, 54, 3.68), (5.81, 58, 4.75)]),
    ("换引擎", "原生 TTS · LLM 在 CPU", [(5.56, 3, 0.18)]),
    ("LLM 上 GPU", "两者同时用 GPU", [(4.19, 10, 2.08), (4.33, 8, 2.69)]),
    ("轮流", "LLM 写完一句等 TTS", [(2.09, 1, 0.13), (2.12, 2, 0.20)]),
]


def mean(values):
    return sum(values) / len(values)


def fmt_half_up(value, digits):
    """Round half up (4.215 -> 4.22), not to the nearest binary float, so the labels match
    what a reader gets by averaging the two runs by hand."""
    from decimal import ROUND_HALF_UP, Decimal
    return str(Decimal(repr(value)).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))


def stages():
    """First audio and gap total for the four stages, both runs drawn."""
    x0, x1 = 150, 410
    metrics = [("首声（s）", 0, 8.0, "{:.2f}"), ("断音合计（s）", 2, 6.0, "{:.2f}")]
    body, y = [], 0
    for title, index, top, fmt in metrics:
        sx = lambda v: x0 + (x1 - x0) * min(v, top) / top
        body.append(t(0, y + 16, title, 15, weight=600))
        body.append(t(480, y + 16, "每阶段两次，各画一条；换引擎一次作废", 12, "muted", "end"))
        y += 28
        for name, note, runs in STAGES:
            body.append(t(0, y + 15, name, 13, weight=600))
            body.append(t(0, y + 31, note, 11, "muted"))
            key = "llm" if name == "轮流" else "neutral"
            for j, run in enumerate(runs):
                value = run[index]
                by = y + 2 + j * 15
                if value:
                    body.append(bar(x0, sx(value), by, 12, key, 2))
                label = fmt.format(value)
                if index == 2:
                    label += f" · {run[1]} 次"
                if value > top:   # off the scale: cut the bar and put the value inside it
                    body.append(f'<line x1="{sx(top) - 8:.1f}" y1="{by - 1}" x2="{sx(top) - 4:.1f}" y2="{by + 13}" '
                                'stroke="{text}" stroke-width="2"/>')
                    body.append(t(sx(top) - 14, by + 10, label + "（超出刻度）", 11, "text", "end", 600))
                else:
                    body.append(t(sx(value) + 6, by + 10, label, 11, "text", "start", 600))
            y += 40
        body.append(f'<line x1="{x0}" y1="{y - 160}" x2="{x0}" y2="{y - 6}" stroke="{{grid}}"/>')
        y += 14
    body.append(t(0, y + 4, "同一问题 · 同一克隆声音 · 独立服务端 · 断音按设备 0.3 s 预缓冲估计", 11, "muted"))
    write("stages", 480, y + 12, body)


def before_after():
    """Start of the journey vs. now, each metric on its own scale."""
    origin, duet = STAGES[0][2], STAGES[-1][2]
    first0, first1 = mean([r[0] for r in origin]), mean([r[0] for r in duet])
    gap0, gap1 = mean([r[2] for r in origin]), mean([r[2] for r in duet])
    panels = [
        ("长回答首声（s）", "越低越好 · 起点→轮流，两次平均", round(first0, 4), round(first1, 4), 7.0, 1),
        ("长回答断音合计（s）", "越低越好 · 起点→轮流，两次平均", round(gap0, 4), round(gap1, 4), 5.0, 2),
        ("句间响度差（dB）", "越低越好 · 逐句整形", 4.6, 3.7, 6.0, 1),
        ("名字唤醒命中（/60）", "越高越好 · 服务端唤醒", 29, 53, 60, 0),
    ]
    body, pw = [], 220
    for i, (title, note, before, after, top, fmt) in enumerate(panels):
        px, py = (i % 2) * (pw + 40), (i // 2) * 140
        body.append(t(px, py + 16, title, 15, weight=600))
        body.append(t(px, py + 36, note, 12, "muted"))
        bx0, bx1 = px + 40, px + pw - 40
        for j, (label, value, key) in enumerate([("之前", before, "neutral"), ("之后", after, "llm")]):
            y = py + 48 + j * 34
            body.append(t(px, y + 17, label, 13, "muted"))
            end = bx0 + (bx1 - bx0) * value / top
            if value:
                body.append(bar(bx0, end, y, 24, key))
            body.append(f'<line x1="{bx0}" y1="{y - 2}" x2="{bx0}" y2="{y + 26}" stroke="{{grid}}"/>')
            body.append(t(end + 6, y + 17, fmt_half_up(value, fmt), 14, "text", "start", 600))
    write("before-after", 480, 262, body)


def bench():
    """Median and min-max per case from bench/results/runs.jsonl."""
    import json
    import statistics
    runs = OUT.parents[1] / "bench" / "results" / "runs.jsonl"
    if not runs.exists():
        return
    rows = [json.loads(line) for line in runs.open(encoding="utf-8")]
    cases = [("long", "plain", "无调度"), ("short", "plain", "仅缩短句子"),
             ("long", "turns", "仅 GPU 轮流"), ("short", "turns", "缩短句子 + 轮流")]
    metrics = [("first_audio_s", "首声（s）"), ("underrun_total_s", "断音合计（s）")]
    x0, x1, body, y = 118, 430, [], 0
    for key, title in metrics:
        groups = [[r[key] for r in rows if r["case"] == c and r["mode"] == m] for c, m, _ in cases]
        top = max(max(g) for g in groups if g) * 1.05 or 1.0
        sx = lambda v: x0 + (x1 - x0) * v / top
        body.append(t(0, y + 16, title, 15, weight=600))
        body.append(t(x1, y + 16, "条 = 中位数 · 线 = 最小～最大", 12, "muted", "end"))
        y += 28
        for (case, mode, label), values in zip(cases, groups):
            if not values:
                continue
            med, lo, hi = statistics.median(values), min(values), max(values)
            key_color = "llm" if mode == "turns" else "neutral"
            body.append(t(x0 - 10, y + 15, label, 13, "text", "end"))
            if med:
                body.append(bar(x0, sx(med), y + 2, 18, key_color))
            body.append(f'<line x1="{sx(lo):.1f}" y1="{y + 11}" x2="{sx(hi):.1f}" y2="{y + 11}" stroke="{{text}}" stroke-width="1.5"/>')
            for v in (lo, hi):
                body.append(f'<line x1="{sx(v):.1f}" y1="{y + 6}" x2="{sx(v):.1f}" y2="{y + 16}" stroke="{{text}}" stroke-width="1.5"/>')
            body.append(t(max(sx(hi), sx(med)) + 8, y + 15, f"{med:.2f}", 13, "text", "start", 600))
            y += 28
        body.append(f'<line x1="{x0}" y1="{y - 112}" x2="{x0}" y2="{y}" stroke="{{grid}}"/>')
        y += 18
    write("bench", 480, y, body)


if __name__ == "__main__":
    timeline()
    vram()
    before_after()
    stages()
    bench()
    print("wrote", sorted(p.name for p in OUT.glob("*.svg")))

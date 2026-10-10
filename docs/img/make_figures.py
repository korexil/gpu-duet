"""Regenerate the README figures (light + dark SVG). No dependencies.

  python docs/img/make_figures.py

Numbers come from docs/pitfalls.md and docs/architecture.md. The timeline is a
schematic; only its annotated values are measured.
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
        ("同时跑：TTS 被拖慢（RTF 1.1～1.6）", 1.60, "断音合计 1.47 s", {
            "llm": [(0, 2.6)],
            "tts": [(0.3, 2.2), (2.25, 4.1), (4.15, 6.0)],
            "play": [(1.6, 3.0), (3.0, 3.5), (3.9, 4.4), (4.75, 5.7)],
            "gap": [(3.5, 3.9), (4.4, 4.75)],
        }),
        ("轮流跑：写完一句就让出 GPU（RTF 约 0.6）", 1.06, "断音 0", {
            "llm": [(0, 0.3), (1.0, 1.3), (2.1, 2.4)],
            "tts": [(0.3, 1.0), (1.3, 2.1), (2.4, 3.2)],
            "play": [(1.06, 2.46), (2.46, 3.86), (3.86, 5.26)],
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
    rows = [("TTS 未做存储优化（推算）", "8.4 GB · 超出", [("TTS", 5.0, "tts"), ("LLM", 2.9, "llm"), ("", 0.5, "neutral")]),
            ("本仓库配置", "7.3 GB", [("TTS", 3.9, "tts"), ("LLM", 2.9, "llm"), ("", 0.5, "neutral")])]
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


def before_after():
    """Four measured improvements, each on its own scale."""
    panels = [
        ("长回答首声（s）", "越低越好 · GPU 轮流", 1.60, 1.06, 2.0, "{:.2f}"),
        ("长回答断音（s）", "越低越好 · GPU 轮流", 1.47, 0.0, 2.0, "{:.2f}"),
        ("句间响度差（dB）", "越低越好 · 逐句整形", 4.6, 3.7, 6.0, "{:.1f}"),
        ("名字唤醒命中（/60）", "越高越好 · 服务端唤醒", 29, 53, 60, "{:.0f}"),
    ]
    body, pw = [], 220
    for i, (title, note, before, after, top, fmt) in enumerate(panels):
        px, py = (i % 2) * (pw + 40), (i // 2) * 140
        body.append(t(px, py + 16, title, 15, weight=600))
        body.append(t(px, py + 36, note, 13, "muted"))
        bx0, bx1 = px + 40, px + pw - 40
        for j, (label, value, key) in enumerate([("之前", before, "neutral"), ("之后", after, "llm")]):
            y = py + 48 + j * 34
            body.append(t(px, y + 17, label, 13, "muted"))
            end = bx0 + (bx1 - bx0) * value / top
            if value:
                body.append(bar(bx0, end, y, 24, key))
            body.append(f'<line x1="{bx0}" y1="{y - 2}" x2="{bx0}" y2="{y + 26}" stroke="{{grid}}"/>')
            body.append(t(end + 6, y + 17, fmt.format(value), 14, "text", "start", 600))
    write("before-after", 480, 262, body)


if __name__ == "__main__":
    timeline()
    vram()
    before_after()
    print("wrote", sorted(p.name for p in OUT.glob("*.svg")))

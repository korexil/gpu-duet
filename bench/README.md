# Benchmark：GPU 轮流 × 句子长短

把「GPU 轮流」和「缩短句子」两个因素拆开，各测一遍：

| | 不轮流（`plain`） | GPU 轮流（`turns`） |
|---|---|---|
| 长句、句间不补停顿 | 无调度 | 仅 GPU 轮流 |
| 短句、句间补到 0.9 s | 仅缩短句子 | 缩短句子 + GPU 轮流 |

- **只差调度。** 每个（问题，种子）在两种模式下各跑一次。每次开跑前清掉 llama-server 的前缀缓存，同一个种子生成的文字逐字相同，结果里有核对。
- **从冷态开始。** 每次开跑前等 10 s，让 GPU 降回空闲频率，这是第一句最慢的情况。两种模式的先后顺序轮换。
- **测什么。** `tools/turn_taking_sim.py`：LLM 流式输出 → 按标点分句 → 逐句调 TTS → 模拟一个缓冲 0.5 s 就开播的播放器。记录首声时间、断音合计、最长一次断档。不经过 xiaozhi 和设备，排除网络和 VAD/ASR。
- **句子长短怎么控制。** 「短句」只在系统提示词里加一句「一文には一つのことだけ。一文は15字くらいまで」。问题和提示词见 `run.py` 开头（日语，因为测试用的声音是日语）。

## 复现

```bash
# 1. tts-service 用 --sentence-pause 0 启动
python bench/run.py --sentences long
# 2. tts-service 改用 --sentence-pause 0.9 重启
python bench/run.py --sentences short
# 3. 汇总
python bench/run.py --summary
```

`results/runs.jsonl` 是逐次的原始记录（含生成的文字），`results/summary.csv` 是汇总，`results/env-*.json` 是测试环境（GPU、驱动、模型、TTS 停顿设置）。[docs/benchmarks.md](../docs/benchmarks.md) 里的 benchmark 图由 `docs/img/make_figures.py` 从 `runs.jsonl` 生成。

测量时 GPU 上只能有这两个进程，见 [踩坑 #26](../docs/pitfalls.md)。

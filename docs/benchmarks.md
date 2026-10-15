# 实测数据

环境：RTX 4060 Laptop 8 GB、16 GB 内存、Windows 11。下面两组数据口径不同：第一组走完整的独立服务端管线，看从起点到现在每一步的效果；第二组只差调度，用来判断哪个做法真正起作用。

首声 = 问题发出到第一帧音频；断音 = 播放器缓冲用完、喇叭没声音的时长之和。

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="img/before-after-dark.svg">
  <img alt="四项指标，之前→之后。长回答首声 6.2→2.1 s、断音合计 4.22→0.17 s：四阶段实测中起点阶段与轮流阶段各两次的平均；句间响度差 4.6→3.7 dB：benchmark 回答上的逐句整形；名字唤醒 29→53/60：板载 MultiNet 最好成绩对服务端 SenseVoice" src="img/before-after-light.svg" width="480">
</picture>

## 四个阶段：同一个问题，首声和断音逐阶段下降

2026-10-14，同一条独立服务端管线、同一把克隆声音、同一个讲故事的问题（要求约 300 字），每个阶段问两次。断音按设备 0.3 s 预缓冲估计（[`standalone/session.py`](../standalone/session.py) 的 `Pacer`）。每次开问前等 10 s 让 GPU 降频。

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="img/stages-dark.svg">
  <img alt="四阶段各两次的首声和断音合计。起点 6.67 / 5.81 s，断音 3.68 s（54 次）/ 4.75 s（58 次）；换引擎 5.56 s，断音 0.18 s（3 次；另一次记录混入了两段回答，作废）；LLM 上 GPU 4.19 / 4.33 s，断音 2.08 s（10 次）/ 2.69 s（8 次）；轮流 2.09 / 2.12 s，断音 0.13 s（1 次）/ 0.20 s（2 次）" src="img/stages-light.svg" width="480">
</picture>

| 阶段 | TTS | LLM | 调度 | 首声 s | 断音合计 s（次数） |
|---|---|---|---|---|---|
| 0 起点 | PyTorch 版，GPU | CPU（`-ngl 0`） | 同时跑 | 6.67 · 5.81 | 3.68（54）· 4.75（58） |
| 1 换引擎 | 本仓库原生引擎，GPU | CPU | 同时跑 | 5.56（另一次作废） | 0.18（3） |
| 2 LLM 上 GPU | 原生引擎，GPU | GPU（`-ngl 99`） | 同时跑（`llm.overlap: true`） | 4.19 · 4.33 | 2.08（10）· 2.69（8） |
| 3 轮流 | 原生引擎，GPU | GPU | 每段合成完再生成下一段 | **2.09 · 2.12** | **0.13（1）· 0.20（2）** |

- 第 0 阶段的断音是 50 多次 0.1～0.5 s 的小空档，听起来每句都一顿一顿；PyTorch 版 TTS 进程私有内存 8.9 GB，16 GB 机器上大半被换到页面文件（[踩坑 #13](pitfalls.md)）。
- 第 1 阶段第一次作废：那条记录里有 15 段、音频 143 s（其他各次 64～88 s），是两段不同的回答交错在一起（两个故事开头、两个结尾），一条记录里装了两个回答（是两个提问同时在跑，还是回放把两段拼在一起，原始记录分不出来）。原记录是首声 5.18 s、断音 28 次共 55.89 s，不能当作这个阶段的结果。
- 第 3 阶段剩下的 0.1～0.2 s 断音出现在第一段音频开始后不久（约 2.3 s 处）。
- README 里的「之前 / 之后」取第 0 阶段和第 3 阶段两次的平均：首声 (6.67 + 5.81) / 2 = 6.24 ≈ 6.2 s → (2.09 + 2.12) / 2 ≈ 2.1 s；断音 (3.68 + 4.75) / 2 = 4.215 ≈ 4.22 s → (0.13 + 0.20) / 2 = 0.165 ≈ 0.17 s（四舍五入）。
- 每阶段只有两次，用来看量级；可比的统计口径见下一节。

## Benchmark：只有 GPU 轮流能把断音压到 0

把「GPU 轮流」和「缩短句子」两个因素拆开的 2×2 对照，每格 6 次，中位数（括号内最小～最大）。同一问题和种子在两种模式下生成的文字逐字相同（12/12 对），差别只来自调度。不经过设备，用 [`tools/turn_taking_sim.py`](../tools/turn_taking_sim.py) 模拟 0.5 s 缓冲的播放器。方法、复现命令和原始数据：[bench/](../bench/README.md)。

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="img/bench-dark.svg">
  <img alt="四种情况的首声和断音合计中位数：无调度 3.20 s / 2.27 s；仅缩短句子 2.62 s / 0.48 s；仅 GPU 轮流 1.83 s / 0；缩短句子 + GPU 轮流 1.42 s / 0" src="img/bench-light.svg" width="480">
</picture>

| 情况 | 句长中位数 | 首声 s | 断音合计 s | 有断音 | 音频长 s |
|---|---|---|---|---|---|
| 无调度 | 40 字 | 3.20（2.89～3.53） | 2.27（1.39～5.12） | 6/6 | 81（67～118） |
| 仅缩短句子（句间 0.9 s 停顿） | 13.5 字 | 2.62（2.06～2.68） | 0.48（0～1.55） | 4/6 | 45（26～80） |
| 仅 GPU 轮流 | 40 字 | 1.83（1.49～1.92） | 0 | 0/6 | 81（67～118） |
| 缩短句子 + GPU 轮流 | 13.5 字 | 1.42（1.29～1.64） | 0 | 0/6 | 45（26～80） |

只缩短句子时断不断音取决于回答多长，见 [踩坑 #9](pitfalls.md)。

## 其他指标

| 指标 | 数值 | 来源 |
|---|---|---|
| 显存合计 | 约 7.3 GB / 8 GB（7,448 / 8,188 MiB；TTS 3.9～4.0 GB、LLM 约 2.8 GB、桌面等 0.4～0.6 GB）；不开省显存补丁时推算约 8.3 GB（7,448 + 488 + 594 MiB） | 下方「复现」；[architecture.md](architecture.md#资源预算实测)、[how-it-works.md](how-it-works.md#1-省显存c-原生-tts-引擎--补丁tts-从约-50-gb-降到约-40-gb) |
| TTS 实时率（单独跑） | 约 0.6（自己编译 0.58～0.61，预编译包 0.62～0.70）；LLM 同时解码时第一句 1.1～1.6 | [native-tts/README.md](../native-tts/README.md)、[踩坑 #9](pitfalls.md) |
| LLM 解码 / 首字 | 61～66 tok/s / 0.1～0.2 s（随提示长短） | [踩坑 #4](pitfalls.md)、下方「复现」 |
| 端到端首声，经 xiaozhi-esp32-server | 文字输入 1.0～1.8 s；语音输入（说完算起，含 VAD + ASR）2.0～2.9 s | [architecture.md](architecture.md#延迟用法-c软件链路从发出文字到收到第一个-opus-包) |
| 句间响度差 | 4.6 → 3.7 dB（逐句整形） | [`leveler.py`](../tts-service/leveler.py) |
| 0.25 s 窗口响度起伏（p90-p10） | 5.8～7.5 → 4.7～6.0 dB（standalone 的 AGC，三段固定回答） | `python tools/agc_check.py`（2026-10-14 复现，三段分别 7.2 → 4.9、7.5 → 6.0、5.8 → 4.7 dB） |
| 名字唤醒（日语名，60 条） | 板载 MultiNet 最好 29/60；服务端 SenseVoice 53/60，误唤醒 0/20 | [踩坑 #20](pitfalls.md) |
| GPU 空闲降频 | 空闲 8 s 后 TTS 首块 0.64 s，热态 0.17 s | [踩坑 #11](pitfalls.md) |

### 复现

显存（tts-service、llama-server 都在跑、空闲时，PowerShell）。GB 按任务管理器的口径，1 GB = 1024 MiB：

```powershell
nvidia-smi --query-gpu=memory.used,memory.total --format=csv
(Get-Counter '\GPU Process Memory(*)\Dedicated Usage').CounterSamples |
    Where-Object CookedValue -gt 100MB | Select-Object InstanceName, @{n='MiB'; e={[int]($_.CookedValue / 1MB)}}
```

2026-10-14 实测：合计 7,448 / 8,188 MiB；tts-service 4,027～4,065 MiB（随刚合成的句子长短变化），llama-server 2,899 MiB，其余进程约 480 MiB。

LLM 解码速度和首字：`/completion` 返回的 `timings.predicted_per_second` 和 `timings.prompt_ms`。

```bash
curl -s http://127.0.0.1:18080/completion -H "Content-Type: application/json" \
  -d '{"prompt": "Tell me a long story about autumn.", "n_predict": 200, "cache_prompt": false}'
```

2026-10-14 复现 10 次（生成 48～400 token、提示 36～408 token）：解码 61.0～63.9 tok/s，prompt 36 token 时 57～84 ms、408 token 时 164～177 ms。早先的记录是 63～65 tok/s（[踩坑 #4](pitfalls.md)）和 66 tok/s（[踩坑 #12](pitfalls.md) 显存溢出之前），文档里统一写作 61～66 tok/s。

图由 [`img/make_figures.py`](img/make_figures.py) 生成。

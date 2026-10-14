# gpu-duet

**让本地 LLM 和声音克隆 TTS 共用一块 8 GB 显卡：边生成边说话，长回答几乎不断音。**

*A local LLM and a zero-shot voice-cloning TTS take turns on one 8 GB GPU. Use them as local HTTP services, or as an offline voice server for ESP32 boards running xiaozhi firmware.*

[![CI](https://github.com/korexil/gpu-duet/actions/workflows/ci.yml/badge.svg)](https://github.com/korexil/gpu-duet/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/korexil/gpu-duet)](https://github.com/korexil/gpu-duet/releases/latest)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

## 能做什么

- **一块 8 GB 显卡装下两个模型**：Gemma 4 E4B（LLM）+ Qwen3-TTS 1.7B（用 3～10 秒录音克隆声音），显存合计约 7.3 GB。
- **LLM 和 TTS 轮流用 GPU**：一分多钟的长回答边生成边播放，断音合计从 4.22 s 降到 0.17 s。
- **逐句整形**：每句合成后统一响度、停顿和峰值，小喇叭上不忽大忽小、不爆音。
- **全部离线，跑在一台 Windows 电脑上**：可以当普通的本地 HTTP 服务用，也可以直接给 ESP32 小智板子当服务端。

## 效果

| | 之前 | 之后 |
|---|---|---|
| 长回答首声（问完到第一声） | 6.2 s | **2.1 s** |
| 长回答断音合计（播放中卡住的时长） | 4.22 s，卡 50 多次 | **0.17 s** |
| 显存合计（8 GB 卡） | 约 8.3 GB，装不下 | **约 7.3 GB** |

<sub>RTX 4060 Laptop 8 GB。首声、断音：同一个长回答问题各问两次取平均，「之前」是 PyTorch 版 TTS + CPU 上的 LLM。显存：「之后」是实测 7,448 MiB，「之前」是再加上补丁省下的 488 + 594 MiB 推算出来的；GB 按任务管理器的口径（1 GB = 1024 MiB）。只改调度的对照实验（每组 6 次）里，轮流前 6/6 次有断音，轮流后 0/6。全部数据：[docs/benchmarks.md](docs/benchmarks.md)。</sub>

## 和小智（xiaozhi）的关系

小智是一个开源的 ESP32 语音助手，分两部分：板子上的 [xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) 固件，和电脑上的 [xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server) 服务端。

**本仓库的核心部件（TTS 引擎、TTS 服务、GPU 轮流、逐句整形）都不依赖小智**，只有接板子时才用到它。三种用法：

| 用法 | 适合 | 要跑的 | 要不要小智 |
|---|---|---|---|
| **A. 不接板子** | 给自己的程序、别的语音框架当本地 LLM + TTS | `tts-service` + `llama-server` | 不要 |
| **B. 独立服务端** | 有小智板子，不想装 Docker、数据库、管理后台 | A + `standalone/server.py` | 只要板子上的固件 |
| **C. 挂进 xiaozhi-esp32-server** | 要管理后台、多设备、声纹、工具调用 | A + 官方 Docker 栈 + [`server/`](server/) 里的 provider | 固件 + 服务端 |

两种接板子方式的详细对比、各部件的依赖：[docs/architecture.md](docs/architecture.md)。

## 原理

```
麦克风 → VAD · ASR（CPU） → LLM（GPU） ⇄ 轮流 ⇄ TTS（GPU） → 喇叭
```

- **省显存**：TTS 换成打了补丁的 C++ 原生引擎，显存约 5.0 → 4.0 GB，给 LLM 腾出位置。
- **轮流用 GPU**：两者同时跑时 TTS 被拖慢，合成赶不上播放。所以 LLM 每写完一句就暂停，等 TTS 合成完这句再接着写。
- **逐句整形**：每句合成后切掉多余静音、统一增益、限幅、补齐停顿。

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/img/timeline-dark.svg">
  <img alt="示意图：同时跑时 TTS 被拖慢，首声 3.20 s、断音合计 2.27 s；轮流跑时首声 1.83 s、断音 0（benchmark 中位数）" src="docs/img/timeline-light.svg" width="480">
</picture>

详细说明和参数：[docs/how-it-works.md](docs/how-it-works.md)。

## 需要什么

| | |
|---|---|
| 显卡 | NVIDIA，8 GB 显存（实测 RTX 4060 Laptop；预编译引擎支持 RTX 20 / 30 / 40 / 50 系） |
| 内存 | 16 GB |
| 系统 | Windows 10 / 11 x64（实测 Windows 11；Linux 只在 CI 里验证到能编译） |
| 磁盘 | 约 13 GB（模型下载约 8.8 GB） |
| 声音 | 一段 3～10 秒的干净录音和它的逐字文本（[怎么准备](docs/voice-reference.md)） |
| 板子（可选） | ESP32-S3，刷 xiaozhi-esp32 固件（实测 M5Stack Atom EchoS3R、Stack-chan） |

## 快速开始

在本仓库根目录的 PowerShell 里执行，需要 Python 3.10+ 和 git（提示「禁止运行脚本」时先 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`）。下面用仓库旁边的 `..\gpu-duet-engine`、`..\models`、`..\llama.cpp` 当例子，换成你自己的目录即可。

**1. 下载引擎、装依赖。** 从 [Releases](https://github.com/korexil/gpu-duet/releases/latest) 下载 zip，解压到 `..\gpu-duet-engine`（SHA256 见 [native-tts/README.md](native-tts/README.md)）。CUDA 运行库用 pip 装，不用装 CUDA Toolkit：

```powershell
pip install -r tts-service\requirements.txt nvidia-cuda-runtime-cu12 nvidia-cublas-cu12
```

**2. 下载模型。** TTS 转成 F16 GGUF 放到 `..\models\tts`，LLM 放到 `..\models\llm`；转换完可删 `..\models\src`。

```powershell
.\native-tts\setup-models.ps1 -Out ..\models -InstallDeps
```

**3. 提取声音。** 录音 `my_voice.wav`，逐字文本写进 `my_voice.txt`（UTF-8）：

```powershell
python tts-service\extract_voice.py --library ..\gpu-duet-engine\qwen3_tts_bridge.dll `
    --models ..\models\tts --wav my_voice.wav --text my_voice.txt --out voice\icl-prompt.json
```

**4. 先启动 TTS，再另开窗口启动 LLM。** 顺序不能反，否则 TTS 可能被挤到共享内存、变慢。`llama-server` 用 [llama.cpp Releases](https://github.com/ggml-org/llama.cpp/releases) 的 Windows CUDA 版。

```powershell
python tts-service\server.py --library ..\gpu-duet-engine\qwen3_tts_bridge.dll `
    --models ..\models\tts --icl-prompt voice\icl-prompt.json --language zh
```

```powershell
.\scripts\start-llm.ps1 -LlamaServer ..\llama.cpp\llama-server.exe `
    -Model ..\models\llm\gemma-4-E4B-it-q4_0-exact.gguf
```

**5. 不接板子试一下。** 输出里 `underrun_total_s` 是断音合计，0 就是没卡：

```powershell
python tools\turn_taking_sim.py --mode turns --question "说说你最喜欢的季节，讲长一点。"
```

到这一步就是用法 A：自己的程序调 `http://127.0.0.1:18766/stream` 合成语音，接口见 [tts-service/README.md](tts-service/README.md)。

**6. 接板子（用法 B）。**

```powershell
pip install -r standalone\requirements.txt
python standalone\setup_models.py      # ASR、VAD、降噪模型，约 240 MB
copy standalone\config.example.yaml standalone\config.yaml
python standalone\server.py
```

把板子的 OTA 地址改成启动日志打出的 `http://<本机IP>:8003/xiaozhi/ota/`，防火墙放行 TCP 8000、8003。改 OTA 地址的办法、没有板子时的测试方法见 [standalone/README.md](standalone/README.md)。

用法 C 见 [server/README.md](server/README.md)；自己编译引擎见 [native-tts/README.md](native-tts/README.md)。

## 已知限制

- 实机只测过 RTX 4060 Laptop + Windows 11；Linux 只在 CI 里验证到能编译，macOS 没测过。
- `llamacpp_paced` 不支持工具调用（MCP、设备音量等）；独立服务端没有声纹、记忆和流式 ASR。
- 句间响度只按每句开头 0.6 s 定增益，不是响度标准。

完整列表：[docs/architecture.md](docs/architecture.md#限制)。

## 提给上游的修复

做这个项目时在上游发现的问题，修好后提了 PR（截至 2026-10-14）：

| 上游 | PR | 内容 | 状态 |
|---|---|---|---|
| [xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server) | [#3411](https://github.com/xinnan-tech/xiaozhi-esp32-server/pull/3411) | FunASR 的 JSON 原样进入对话历史 | 已合并 |
| | [#3413](https://github.com/xinnan-tech/xiaozhi-esp32-server/pull/3413) | TTS 分句在引号内切开句子 | 已合并 |
| | [#3412](https://github.com/xinnan-tech/xiaozhi-esp32-server/pull/3412) | SenseVoice 日语识别结果按词带空格 | 待合并 |
| [qwen3-tts.cpp](https://github.com/Danmoreng/qwen3-tts.cpp) | [#5](https://github.com/Danmoreng/qwen3-tts.cpp/pull/5) | 模型、声音提示、参考音路径按 UTF-8 打开（中文、日文目录） | 待回复 |
| | [#6](https://github.com/Danmoreng/qwen3-tts.cpp/pull/6) | C 边界捕获异常、导出结构体 ABI 表、C 桥接库 | 待回复 |

另有 3 个分支已备好，等 #5、#6 有回应再提：Q/K/V 去重（GPU −488 MiB）、文本表放 CPU（GPU、内存各 −594 MiB）、ICL 提示 JSON 的 `\uXXXX` 解码。详情：[docs/upstream.md](docs/upstream.md)。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/how-it-works.md](docs/how-it-works.md) | 原理：省显存补丁、GPU 轮流的时序和参数、逐句整形 |
| [docs/benchmarks.md](docs/benchmarks.md) | 实测数据和测量方法 |
| [docs/architecture.md](docs/architecture.md) | 三种用法、部件依赖、端口和资源、运行环境、限制 |
| [tts-service/README.md](tts-service/README.md) | TTS 服务的启动参数和 HTTP 接口 |
| [native-tts/README.md](native-tts/README.md) | 引擎补丁开关、预编译包、自己编译、模型 |
| [standalone/README.md](standalone/README.md) | 独立服务端：连板子、配置、实时面板 |
| [server/README.md](server/README.md) | 接入 xiaozhi-esp32-server |
| [docs/voice-reference.md](docs/voice-reference.md) | 怎么准备参考音 |
| [docs/pitfalls.md](docs/pitfalls.md) | 踩坑 32 条 |
| [docs/standalone-tuning.md](docs/standalone-tuning.md) | 独立服务端的调试记录 |
| [docs/upstream.md](docs/upstream.md) | 提给上游的 PR、实验分支 |
| [docs/development.md](docs/development.md) | 目录结构、测试（不需要显卡和模型）、CI |
| [bench/README.md](bench/README.md) | benchmark 复现 |

## 致谢

[llama.cpp](https://github.com/ggml-org/llama.cpp)、[qwen3-tts.cpp](https://github.com/Danmoreng/qwen3-tts.cpp)、[Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS)、[Gemma](https://ai.google.dev/gemma)、[xiaozhi-esp32](https://github.com/78/xiaozhi-esp32)、[xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server)、[sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)。

## 许可

[Apache-2.0](LICENSE)，署名见 [NOTICE](NOTICE)。分发或修改时保留 NOTICE，并在改过的文件里注明。上游各自适用自己的许可：xiaozhi-esp32-server、qwen3-tts.cpp、ggml（MIT）；Qwen3-TTS、Gemma 4（Apache-2.0）。本仓库不分发模型和 CUDA 运行库。用他人的声音做参考音前，先取得本人同意。

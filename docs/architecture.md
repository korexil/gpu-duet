# 架构

## 三种用法和各部件的依赖

小智（xiaozhi）指两样东西：板子上的 [xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) 固件，和电脑上的 [xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server) 服务端。本仓库的核心部件两样都不需要。

| 用法 | 跑什么 | 适合 |
|---|---|---|
| **A. 不接板子** | `tts-service` + `llama-server`，自己的程序调 `POST /stream`；要 GPU 轮流就照 `llamacpp_paced.py` 的方式续写（见 [how-it-works.md](how-it-works.md#在自己的程序里用)） | 别的语音助手、桌面程序、机器人框架 |
| **B. 独立服务端** | A + `python standalone/server.py`；板子只需刷 xiaozhi-esp32 固件、把 OTA 地址指向这台电脑 | 接板子，但不需要 Docker、数据库和管理后台 |
| **C. 挂进 xiaozhi-esp32-server** | A + 官方 Docker 栈，`llamacpp_paced` 作为 LLM provider，TTS 选 `index_stream` | 需要管理后台、多设备管理、声纹、设备侧工具调用时 |

接板子的两种用法（B、C）共用同一对 GPU 进程（`llama-server`、`tts-service`），只是对话服务端不同；用法 A 只有这两个进程。

| 部件 | 依赖 | 不依赖 |
|---|---|---|
| [`native-tts/`](../native-tts/) 引擎补丁 | qwen3-tts.cpp @ `16bb5af`、ggml、CUDA | 小智的任何东西 |
| [`tts-service/`](../tts-service/) HTTP TTS 服务 | `native-tts` 编出的 `qwen3_tts_bridge.dll`、numpy、soundfile | 小智；接口是普通 HTTP（`POST /stream`、`GET /health`） |
| [`tts-service/leveler.py`](../tts-service/leveler.py)、[`tail_gate.py`](../tts-service/tail_gate.py) 逐句整形 | numpy | 小智；输入输出都是 float32 PCM |
| [`llamacpp_paced.py`](../server/core/providers/llm/llamacpp_paced/llamacpp_paced.py) GPU 轮流续写 | 运行时只调 `llama-server` 的 `/apply-template`、`/completion`、`/detokenize` 和 TTS 的 `/health`；文件里 import 了 xiaozhi 的 provider 基类和 logger 两个名字 | 小智服务端：这两个 import 可以换成替身直接加载，[`standalone/brain.py`](../standalone/brain.py) 和 [`tools/turn_taking_sim.py`](../tools/turn_taking_sim.py) 就是这样用的 |
| [`standalone/`](../standalone/) 独立服务端 | 板子上的 xiaozhi-esp32 固件（自己实现了固件的 WebSocket 协议 v1、OTA 应答）；sherpa-onnx、onnxruntime、libopus、websockets | xiaozhi-esp32-server、Docker、数据库、管理后台 |
| [`server/`](../server/) provider 挂载、`wakeGate.py`、补丁 | xiaozhi-esp32-server（补丁基线 `a6cd755`） | — |
| [`bench/`](../bench/)、`tools/turn_taking_sim.py` | `llama-server` + `tts-service` | 小智、板子 |

## 部件、端口和资源

ASR、LLM、TTS 全部在一台 Windows 笔记本上，板子只负责收音和播放。

| 部件 | 进程 / 设备 | 模型 | 显存 | 内存 | 端口 / 接口 |
|---|---|---|---|---|---|
| 终端 | ESP32-S3，[xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) 固件（实测 M5Stack Atom EchoS3R、Stack-chan） | 板载唤醒（ESP-SR） | — | — | 连服务端 `ws://<主机>:8000/xiaozhi/v1/`；开机先访问 OTA 地址拿 WebSocket 地址 |
| 对话服务端 | 二选一：[`standalone/server.py`](../standalone/)（一个 Python 进程）或 [xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server)（Docker）+ [`server/`](../server/) | Silero VAD（约 2 MB）、SenseVoice int8（约 240 MB）、GTCRN 降噪（0.5 MB，仅 standalone），都在 CPU | 0 | standalone 约 1 GB；Docker 栈约 3.4 GB | 8000 WebSocket；8003 HTTP（`/xiaozhi/ota/`；standalone 另有 `/status`、`/dashboard`、`/events`） |
| LLM 客户端 | [`llamacpp_paced.py`](../server/core/providers/llm/llamacpp_paced/llamacpp_paced.py)，在对话服务端进程内 | — | — | — | 调 llama-server；轮询 TTS `/health` |
| LLM | llama.cpp `llama-server`（Windows CUDA 版） | Gemma 4 E4B QAT q4_0，全层 GPU，`-c 8192` | 约 2.8 GB | 约 1～2.6 GB | `127.0.0.1:18080`：`/apply-template`、`/completion`、`/detokenize` |
| TTS | [`tts-service/server.py`](../tts-service/) + 补丁版 qwen3-tts.cpp 的 `qwen3_tts_bridge.dll`（ctypes） | Qwen3-TTS 12Hz 1.7B Base，F16 GGUF（talker + tokenizer 约 4.2 GB） | 3.9～4.0 GB | 约 0.5 GB 常驻 + 约 0.6 GB 只读映射 | `127.0.0.1:18766`：`POST /stream`、`POST /synthesize`、`GET /health` |
| 桌面、浏览器等 | — | — | 0.4～0.6 GB | — | — |
| **合计（GPU）** | | | **约 7.3 GB / 8 GB** | | |

## 用法 C：挂进 xiaozhi-esp32-server（Docker）

```
 ESP32-S3 设备（xiaozhi 固件）                 Windows 笔记本（RTX 4060 Laptop 8 GB / 16 GB）
 ┌────────────────────┐   WebSocket     ┌──────────────────────── Docker ─────────────────────────┐
 │ 麦克风 → Opus 上行  │ ──────────────▶ │ xiaozhi-esp32-server                                    │
 │ 喇叭  ← Opus 下行   │ ◀────────────── │  VAD(Silero) → ASR(SenseVoice, CPU) → wakeGate          │
 │ 板载唤醒(MultiNet)  │   MCP: 音量      │        → LLM provider: llamacpp_paced ─┐                │
 │ 只用来开启会话       │                 │        → TTS provider: index_stream ─┐ │                │
 └────────────────────┘                 └───────────────────────────────────────┼─┼────────────────┘
                                                                                 │ │ host.docker.internal
                                    ┌────────────────────────────────────────────▼─┼──────────────┐
                                    │ tts-service（原生 Qwen3-TTS 1.7B F16）  :18766  │  GPU ~4.0 GB  │
                                    │   /stream  /health{busy}  ← leveler.py         │              │
                                    └──────────────────────────────▲─────────────────┘              │
                                                         轮流用 GPU │ busy?                          │
                                    ┌──────────────────────────────┴──────────────────────────────┐ │
                                    │ llama-server（Gemma 4 E4B QAT q4_0，全层 GPU）  :18080        │◀┘
                                    │   /apply-template  /completion  /detokenize   GPU ~2.8 GB   │
                                    └─────────────────────────────────────────────────────────────┘
```

## 用法 B：独立服务端（standalone）

```
 ESP32-S3 设备（xiaozhi 固件）              standalone/server.py（一个 Python 进程，CPU）
 ┌────────────────────┐  ws :8000        ┌──────────────────────────────────────────────────────┐
 │ 麦克风 → Opus 16 kHz │ ───────────────▶ │ Session：半双工（说话时 + guard_ms 丢上行帧）          │
 │ 喇叭  ← Opus 24 kHz │ ◀─────────────── │  Silero VAD → GTCRN 降噪 → SenseVoice（sherpa-onnx）  │
 │ 开机 OTA → :8003    │  http :8003      │  → Brain（加载 llamacpp_paced）→ 分句 → tts_client    │
 └────────────────────┘  /xiaozhi/ota/   │  → 段尾修剪 → AGC → Opus 60 ms 帧（0.3 s 预缓冲节拍）  │
                         /status          │ dashboard：/dashboard 页面 + /events（SSE）           │
                         /dashboard       └──────────────┬──────────────────────────┬────────────┘
                         /events                         │ /completion              │ POST /stream
                                              llama-server :18080         tts-service :18766
```

standalone 里 LLM 的输出是在合成线程里逐段拉取的：一段文字交给 `/stream` 合成完，才继续拉下一段，所以两者天然串行，`llm.turn_taking` 默认关；`llm.overlap: true` 时另起线程让 LLM 一直往前写，用来复现不轮流的情况。

## 资源预算（实测）

| 组件 | GPU | 内存 |
|---|---|---|
| 原生 TTS（F16，开启 QKV 去重、文本表放 CPU 并做只读映射） | 3.9～4.0 GB（随句子长短略有变化） | 约 0.5 GB 常驻（另有约 0.6 GB 只读映射） |
| llama-server，Gemma 4 E4B q4_0，`-c 8192` | 约 2.8 GB | 约 1～2.6 GB（PLE 表按需读取） |
| Windows 桌面、浏览器等 | 0.4～0.6 GB | — |
| 用法 C：Docker（WSL：MySQL、Redis、Java 管理端、Python 服务端加 SenseVoice） | — | 约 3.4 GB |
| 用法 B：standalone（SenseVoice int8 约 240 MB 模型 + VAD + GTCRN，CPU） | — | 约 1 GB（进程私有内存，2026-10-14 实测 961 MB） |
| **合计（GPU）** | **约 7.3 GB（卡上 8 GB）** | |

显存按任务管理器的口径（1 GB = 1024 MiB），2026-10-14 实测：tts-service 4,027～4,065 MiB、llama-server 2,899 MiB、合计 7,448 / 8,188 MiB，复现命令见 [benchmarks.md](benchmarks.md#复现)。模型、下载量这类文件大小按十进制 GB（和 Hugging Face 页面一致）。

## 一轮对话的流程

1. 设备一直在上传 Opus；VAD 判断一句话说完（停顿 0.7 s）后识别（用法 B 先过 GTCRN 降噪）。
2. 用法 C 打了补丁时，`wakeGate`：没叫名字、也不在回应窗口内，就丢弃；叫了名字，就去掉名字只留请求，交给 LLM。用法 B：识别结果等于唤醒词时只应一声 `wake.ack`，其他都交给 LLM。
3. `llamacpp_paced`：每次续写 16 个 token；到句末或满 48 个 token，就等 TTS 接走这句（`busy` 变 true 或 `started` 计数加一，最多等 `handoff_wait` 0.4 s），再等它不再 `busy` 才继续（用法 B 默认不做这步握手，见上）。
4. 分句器按标点切句，逐句调用 TTS 的 `/stream`。用法 B 的分句：第一段过 `first_min_chars`（12）字遇句末就切，之后攒够 `min_chars`（30）字才切，超过 `max_chars`（70）字在逗号处切；`first_cut_chars` > 0 时第一段可以在逗号处提前切出（默认 0，不用）。
5. `leveler.py`：切掉开头静音、定一个整句增益、限幅、补齐句尾停顿；`tail_gate.py` 裁掉句尾的呼吸声。
6. 服务端编码成 Opus 发给设备。用法 C：说完后回应窗口重新计时，窗口结束时道别一次，然后回到等待名字的状态。用法 B：段尾静音修剪到 `chunk_pause_ms`，过慢速 AGC，按 60 ms 一帧的节拍发送；说话期间和之后 0.5 s 丢弃麦克风帧（服务端半双工）。

## 延迟（用法 C，软件链路，从发出文字到收到第一个 Opus 包）

文字轮 1.0～1.8 s（含 LLM、TTS 第一句、服务端 0.5 s 起播缓冲）；语音轮从上传结束算起 2.0～2.9 s（另含 VAD 判停和 ASR）。用法 B 的长回答首声见 [benchmarks.md](benchmarks.md) 的四阶段实测（轮流阶段 2.09 / 2.12 s）。

## 用法 B 和用法 C 的差别

| | [`standalone/`](../standalone/README.md) | 挂进 xiaozhi-esp32-server（[`server/`](../server/README.md)） |
|---|---|---|
| 进程 | 一个 Python 进程，无 Docker、数据库、管理后台 | 官方 Docker 栈（MySQL、Redis、Java 管理端、Python 服务端） |
| 额外内存 | 约 1 GB | 约 3.4 GB |
| ASR / VAD | SenseVoice（sherpa-onnx）+ Silero VAD，CPU；可选 GTCRN 降噪 | xiaozhi 自带 |
| 回声 | 服务端半双工：说话时和之后 `guard_ms`（500 ms）丢弃麦克风帧，固件 AEC 开不开都行 | 依赖固件和 xiaozhi 配置 |
| 唤醒 | 板载唤醒；会话中识别结果等于唤醒词时只应一声 `wake.ack`（启动时合成并缓存） | 可选服务端唤醒闸门 `wakeGate.py`（名字识别、回应窗口、道别） |
| 多设备、声纹、管理后台、工具调用 / MCP | 多设备可以直连（各自有对话历史，LLM 和 TTS 排队）；其余没有，`mcp` 消息忽略 | 有（工具调用不经 `llamacpp_paced`） |
| 接入方式 | `python standalone/server.py` | 挂载一个文件夹 + 两段配置，不用重建镜像；服务端唤醒和设备音量要打补丁并从源码构建（基线 `a6cd755`） |

## 运行环境

| | 实测环境 | 说明 |
|---|---|---|
| GPU | RTX 4060 Laptop，8 GB | 8 GB 是下限：超出时 Windows 驱动不报错，把一部分显存挪到共享内存，LLM 解码 66 → 45 tok/s（[踩坑 #12](pitfalls.md)）。预编译引擎含 sm 75 / 86 / 89 / 120，实机只测过 RTX 40 系 |
| 内存 | 16 GB | standalone 够用；xiaozhi-esp32-server 的 Docker 栈另占约 3.4 GB（[踩坑 #13](pitfalls.md)） |
| 系统 | Windows 11 x64，驱动 591.59 | 预编译引擎用 CUDA 12.8 编译；CUDA 运行库用 NVIDIA 的 pip wheel，不用装 CUDA Toolkit |
| 软件 | Python 3.10+、git、llama.cpp `llama-server`（Windows CUDA 版） | CI 在 Linux 和 Windows 上编 CPU 版桥接库并核对 ABI；Linux 上没有带模型跑过 |
| 模型 | 见上面的部件表，`native-tts/setup-models.ps1` 下载并转换 | 下载约 8.8 GB，占盘约 13 GB |
| 声音 | 3～10 秒干净录音 + 逐字文本 | 参考音的语速、语调会被照搬，见 [voice-reference.md](voice-reference.md) |

## 限制

- 实机只测过 RTX 4060 Laptop + Windows 11；Linux 只在 CI 里验证到能编译，macOS 没测过（CI 另在 Linux 上核对 ABI）。
- `llamacpp_paced` 不支持工具调用；standalone 忽略 `mcp` 消息，没有声纹、记忆、流式 ASR（整句说完才识别）、固件升级分发。
- 句间响度只按每句开头 0.6 s 的 RMS 定增益，4.6 → 3.7 dB，不是响度标准。
- 独立服务端只适合受信任的局域网：明文 `ws://` / `http://`，没有 TLS 和限流；token 默认为空。面板等会显示对话内容的端点默认只对本机开放。见 [standalone/README.md](../standalone/README.md#安全)。
- LLM 用的是非官方的 `xbill9/gemma-4-E4B-it-qat-q4_0-exact-gguf`；Google 官方 QAT GGUF 没测过。

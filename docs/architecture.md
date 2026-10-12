# 架构

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
                                    │ tts-service（原生 Qwen3-TTS 1.7B F16）  :18766  │  GPU ~3.9 GB  │
                                    │   /stream  /health{busy}  ← leveler.py         │              │
                                    └──────────────────────────────▲─────────────────┘              │
                                                         轮流用 GPU │ busy?                          │
                                    ┌──────────────────────────────┴──────────────────────────────┐ │
                                    │ llama-server（Gemma 4 E4B QAT q4_0，全层 GPU）  :18080        │◀┘
                                    │   /apply-template  /completion  /detokenize   GPU ~2.9 GB   │
                                    └─────────────────────────────────────────────────────────────┘
```

## 资源预算（实测）

| 组件 | GPU | 内存 |
|---|---|---|
| 原生 TTS（F16，开启 QKV 去重、文本表放 CPU 并做只读映射） | 3.7～3.9 GB | 约 0.5 GB 常驻（另有约 0.6 GB 只读映射） |
| llama-server，Gemma 4 E4B q4_0，`-c 8192` | 约 2.9 GB | 约 1～2.6 GB（PLE 表按需读取） |
| Windows 桌面、浏览器等 | 0.4～0.6 GB | — |
| Docker（WSL：MySQL、Redis、Java 管理端、Python 服务端加 SenseVoice） | — | 约 3.4 GB |
| **合计** | **约 7.3 / 8.2 GB** | |

## 一轮对话的流程

1. 设备一直在上传音频；VAD 判断一句话说完后，SenseVoice 识别。
2. `wakeGate`：没叫名字、也不在回应窗口内，就丢弃；叫了名字，就去掉名字只留请求，交给 LLM。
3. `llamacpp_paced`：每次续写 16 个 token；到句末或满 48 个 token，就等 TTS 接走这句（`busy` 变 true 或 `started` 计数加一，最多等 `handoff_wait` 0.4 s），再等它不再 `busy` 才继续。
4. xiaozhi 的分句器按标点切句，逐句调用 TTS 的 `/stream`。
5. `leveler.py`：切掉开头静音、定一个整句增益、限幅、补齐句尾停顿。
6. 服务端编码成 Opus 发给设备；说完后回应窗口重新计时。窗口结束时道别一次，然后回到等待名字的状态。

## 延迟（软件链路，从发出文字到收到第一个 Opus 包）

文字轮 1.0～1.8 秒（含 LLM、TTS 第一句、服务端 0.5 秒起播缓冲）；语音轮从上传结束算起 2.0～2.9 秒（另含 VAD 判停和 ASR）。

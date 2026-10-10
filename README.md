# gpu-duet

**本地 LLM 和声音克隆 TTS，在同一块 8 GB 显卡上轮流跑。**

8 GB 显卡、16 GB 内存的笔记本，**完全离线**跑「LLM + 1.7B 声音克隆 TTS」的实时语音对话，不卡顿。声音用你自己准备的参考音。

- **不依赖云端**：ASR、LLM、TTS 全在本机，断网可用。
- **8 GB 显存放得下**：LLM 约 2.9 GB + TTS 约 3.9 GB，GPU 合计约 7.3 GB。
- **不卡顿**：LLM 和 TTS 轮流用 GPU，50 秒级的长回答供音无断档。
- **声音自备**：零样本克隆，3～10 秒参考音即可。

终端用的是 [xiaozhi-esp32](https://github.com/78/xiaozhi-esp32)，但核心部分（GPU 轮流、原生 TTS、逐句整形）和它无关，可以接到任何逐句调用 TTS 的语音管线上。

## 实测

RTX 4060 Laptop 8 GB · 16 GB RAM · Windows 11

| 指标 | 结果 |
|---|---|
| 首声延迟（文字输入 → 第一个音频包） | 1.0～1.8 s |
| 首声延迟（说完 → 第一个音频包，含 VAD + ASR） | 2.0～2.9 s |
| 长回答供音断档（60 ms 播放器欠载） | 0 |
| LLM 解码 | 63～66 tok/s |
| TTS RTF | 约 0.6 |
| 句间响度差（平均） | 4.6 dB → 3.7 dB；峰值 -6 dBFS；爆音 0 |
| 非中英文名字唤醒 | 板载 MultiNet 最好 29/60 → 服务端识别 53/60；误唤醒 0/20 |

## 组成

| 层 | 用的是 | 通用 |
|---|---|---|
| LLM | Gemma 4 E4B QAT q4_0 + llama.cpp `llama-server`，全层 GPU | ✓ 任意 GGUF |
| TTS 引擎 | Qwen3-TTS-12Hz-1.7B-Base F16，C++/ggml + CUDA（`native-tts/`） | ✓ |
| TTS 服务 | HTTP 流式、`busy` 状态、逐句整形（`tts-service/`） | ✓ |
| GPU 轮流 | LLM 每到句末查 TTS 的 `busy`，空闲再续写 | ✓ 协议见下 |
| ASR / 编排 | xiaozhi-esp32-server（Docker）+ 补丁（`server/`） | xiaozhi |
| 唤醒 | 服务端唤醒闸门（`server/core/handle/wakeGate.py`） | xiaozhi |

## 核心做法

1. **LLM 和 TTS 轮流用 GPU。** 同卡并发时 TTS 的 RTF 从 0.6 升到 1.1～1.6，跟不上实时；给 LLM 限速无效。改成 LLM 写完一句就让出 GPU，TTS 空闲再继续。
2. **TTS 换成 C++ 原生引擎。** 测过的配置下 PCM 与原版逐字节相同，GPU 少约 1.1 GB，内存少约 0.6 GB。
3. **逐句整形。** 统一每句增益、切开头静音、统一句尾停顿、平滑限幅、防功放上电 pop。
4. **唤醒放到服务端**（可选）。ESP-SR 不支持的名字，交给服务端 ASR 判断。

24 条踩坑和数据：[docs/pitfalls.md](docs/pitfalls.md)　结构与资源预算：[docs/architecture.md](docs/architecture.md)

## 快速开始

```text
1. 编译 TTS 引擎                native-tts/README.md
2. 用自己的参考音提取声音提示     native-tts/README.md §3
3. 启动 TTS 服务（先于 LLM）      tts-service/README.md
4. 启动 LLM                     scripts/start-llm.ps1
5. 接入 xiaozhi-esp32-server     server/README.md
6. 端到端测量                    tools/device_probe.py · tools/loudness_stats.py
```

## 接到别的管线

只在 xiaozhi 上实测过。换框架时要实现的只有两边：

- **TTS**：`POST /stream {"text": "..."}` 返回 24 kHz 16-bit 单声道 PCM 流；`GET /health` 返回 `{"busy": bool}`。`tts-service/` 已经是这个接口。
- **LLM 客户端**：用 llama-server `/completion` 分块续写，prompt 为 `[渲染好的模板, *已生成 token id]`；每到句末或满 48 token 轮询 `/health`，`busy == false` 再继续。参考 `server/core/providers/llm/llamacpp_paced/llamacpp_paced.py`（130 行，只 import 了 xiaozhi 的 provider 基类和 logger）。

先用 `tools/turn_taking_sim.py` 验证：它脱离 xiaozhi 加载同一个 provider，模拟逐句播放，输出首声时间、总欠载和最大断档。

## 目录

```
docs/         architecture.md · pitfalls.md
native-tts/   qwen3-tts.cpp 补丁 + Windows CUDA 编译脚本
tts-service/  HTTP TTS 服务
scripts/      llama-server 启动参数
server/       xiaozhi-esp32-server 补丁（基于 a6cd755）、新增模块、配置示例
tools/        device_probe · loudness_stats · turn_taking_sim · wake_test · esp32/xiaozhi_assets
```

## 已提交给上游的修复

- [#3411](https://github.com/xinnan-tech/xiaozhi-esp32-server/pull/3411) FunASR 的 JSON 原样进入对话历史
- [#3412](https://github.com/xinnan-tech/xiaozhi-esp32-server/pull/3412) SenseVoice 日语识别结果按词带空格
- [#3413](https://github.com/xinnan-tech/xiaozhi-esp32-server/pull/3413) TTS 分句在引号内切开句子

## 许可

MIT。上游各自适用自己的许可：xiaozhi-esp32-server（MIT）、qwen3-tts.cpp（MIT）、Qwen3-TTS（Apache-2.0）、Gemma（Gemma Terms of Use）。用他人的声音做参考音前，先取得本人同意。

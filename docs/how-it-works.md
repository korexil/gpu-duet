中文 · [English](how-it-works.en.md)

# 原理

三个做法分别解决：显存不够、GPU 争抢、逐句合成的音频不连贯。

## 1. 省显存：C++ 原生 TTS 引擎 + 补丁，TTS 从约 5.0 GB 降到约 4.0 GB

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="img/vram-dark.svg">
  <img alt="显存：TTS 未做存储优化时约 5.0 GB（由补丁节省量推算），合计约 8.3 GB 超出 8 GB；本仓库配置 TTS 约 4.0 GB，合计约 7.3 GB" src="img/vram-light.svg" width="480">
</picture>

TTS 用 [qwen3-tts.cpp](https://github.com/Danmoreng/qwen3-tts.cpp) @ `16bb5af` 加本仓库补丁（[`native-tts/qwen3-tts-cpp-16bb5af.patch`](../native-tts/qwen3-tts-cpp-16bb5af.patch)）。`tts-service/server.py` 启动时默认打开下列开关；测过的种子、文本和参数下，PCM 与关闭存储开关时逐字节相同。

| 改动 | 开关 | 收益 |
|---|---|---|
| Talker Q/K/V 只保留融合后的一份 | `QWEN3_TTS_QKV_DEDUP=1` | GPU −约 488 MiB |
| 文本 embedding 表留在 CPU，只在 CPU 上查表 | `QWEN3_TTS_TEXT_EMBD_CPU=1` | GPU −约 594 MiB |
| 上面这张表改为只读内存映射（按行范围，不预读、不复制） | `QWEN3_TTS_TEXT_EMBD_MMAP=1` | RSS −约 594 MiB |
| 解码器按需批处理 | `QWEN3_TTS_LAZY_BATCH_DECODER=1` | — |
| 结束符后再喂 3 帧静音的 codec 码，让声码器渲染出尾音（重复最后一帧会听出尾音叠一下） | `QWEN3_TTS_TAIL_PAD_FRAMES=3`（0 关闭） | 句尾不再被掐断（约 0.24 s）（预编译包 v0.1.2 不含，需自己编译） |
| C ABI 桥接库 `qwen3_tts_bridge` + 流式回调 | CMake `-DQWEN3_TTS_BUILD_C_BRIDGE=ON` | Python ctypes 直接调用，边合成边出 PCM |
| ABI 自检：结构体大小、每个字段的偏移和类型码 | — | 绑定和库版本不一致时加载即报错 |
| 导出函数在 C 边界捕获 C++ 异常 | — | 引擎出错返回错误信息，不带崩 Python 进程 |
| 模型、声音提示、参考音路径按 UTF-8 打开（`_wfopen` / `CreateFileW`） | — | 中文、日文目录名可用 |

开关取值规则、ABI 校验细节、编译方法见 [native-tts/README.md](../native-tts/README.md)。

LLM 侧：Gemma 4 E4B 全层上 GPU，逐层 embedding 表留在内存按需读（`--lazy-mode on`，否则加载时 16 GB 机器可用内存降到 0.5 GB 以下）。**先起 TTS 再起 LLM**，否则显存可能被 LLM 先占，TTS 被挪到共享内存（[踩坑 #12](pitfalls.md)）。参数见 [`scripts/start-llm.ps1`](../scripts/start-llm.ps1)。

## 2. GPU 轮流：LLM 每写完一句就暂停，等 TTS 合成完再续写

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="img/timeline-dark.svg">
  <img alt="示意图，标注值来自 benchmark 中位数：同时跑时 TTS 实时率 1.1～1.6，首声 3.20 s、断音合计 2.27 s；轮流跑时实时率约 0.6，首声 1.83 s、断音 0" src="img/timeline-light.svg" width="480">
</picture>

LLM 同时解码时，TTS 第一句实时率升到 1.1～1.6，比播放慢（推测是因为 TTS 逐帧解码、由大量很小的同步 kernel 组成，没有用 profiler 验证）。给 LLM 限速（20、14 tok/s）和调 WDDM 调度优先级都无效（[踩坑 #9](pitfalls.md)），所以串行：LLM 写到句末就停，等 TTS 合成完再续写。

一轮回答里 `llamacpp_paced` 和两个服务的交互顺序：

1. `POST /apply-template`：整段对话只渲染一次模板。
2. 循环，直到结束或到 `max_tokens`：
   1. `POST /completion`，`prompt = [渲染好的模板, ...已生成的 token id]`，`n_predict = 16`。前缀不变，KV 缓存命中，只算新增的 16 个 token。
   2. `POST /detokenize`，新文字交给分句器。
   3. 到句末或满 48 个 token 时：`GET /health` 最多等 0.4 s，看 TTS 有没有接走这句（`busy` 变 true 或 `started` 加一）；接走后每 20 ms 轮询一次，`busy` 为 false 才继续（单句最多等 30 s）。

细节：

- 续写用 token id，不用文字：chat 接口的 assistant 预填会被模板去掉末尾空白，文字重新切词也会漂移（[踩坑 #10](pitfalls.md)）。
- `stop_type == "eos"` 时最后一个 token 会被还原成 `<turn|>` 之类的文字，先去掉。
- 多字节字符被切在两块之间时先扣住，下一块补全再输出。
- `/health` 的 `started` 计数让短到两次轮询之间就合成完的句子也能被看见；探测失败按空闲处理，不阻塞回答。
- standalone 里 LLM 输出是在合成线程里逐段拉取的（一段合成完才拉下一段文字），本身就串行，所以 `llm.turn_taking` 默认关；`/health` 握手用在 xiaozhi-esp32-server 那条接法上。

实现：[`llamacpp_paced.py`](../server/core/providers/llm/llamacpp_paced/llamacpp_paced.py)（163 行）。

| 参数 | 默认 | 说明 |
|---|---|---|
| `chunk_tokens` | 16 | 每次 `/completion` 续写的 token 数 |
| `turn_tokens` | 48 | 没遇到句末时，最多写这么多 token 就交给 TTS |
| `handoff_wait` | 0.4 s | 交出一句后等 TTS 接走的最长时间 |
| `max_tokens` | provider 400，standalone 1500 | 一轮回答的上限 |
| `question_rest_note` | — | 可选。上一句回答以问句结尾时，追加到最后一条用户消息上，不破坏前缀缓存（[踩坑 #7](pitfalls.md)） |

不支持工具调用：`response_with_functions` 忽略 `functions`，只记一条日志。

### 在自己的程序里用

不接板子（用法 A）或换别的语音框架时，需要对接的只有两边：tts-service 的 HTTP 接口（见 [tts-service/README.md](../tts-service/README.md)），和照上面的时序向 llama-server 分块续写。

`llamacpp_paced.py` 只 import 了 xiaozhi 的 provider 基类和 logger 两个名字，换成替身就能直接加载：[`standalone/brain.py`](../standalone/brain.py) 和 [`tools/turn_taking_sim.py`](../tools/turn_taking_sim.py) 都是这样用的。握手靠轮询：交出一句后最多等 `handoff_wait` 秒看 TTS 有没有接走；分句器经常攒得更久时调大它，否则两者又会重叠。

## 3. 逐句整形：统一响度、停顿和峰值

逐句合成的音频直接拼起来，在小喇叭上的问题和处理：

| 听到的 | 原因 | 处理 | 在哪 |
|---|---|---|---|
| 一句大一句小（差 5～7 dB） | 每句响度由模型随机决定 | 用开头 0.6 s 有声部分定一个增益（目标 -18 dBFS，最多 +9 dB），整句不变 | `tts-service/leveler.py` |
| 停顿忽长忽短（0.2～1.25 s） | 模型自带 0.4～0.7 s 开头静音，句尾不一 | 切掉开头静音；句尾补到 `--sentence-pause`（默认 0.9 s），`--clause-chars`（默认 6）字以内的短应答 0.5 s | `leveler.py` |
| 爆音、破音 | 峰值接近满幅，Opus 还会冲高 | 1 ms 块平滑限幅到 -6 dBFS，-3 dBFS 再硬限 | `leveler.py` |
| 开口前「啪」一声 | 设备空闲关功放 | 静默 1.5 s 以上后的第一句前留 0.3 s | `leveler.py` |
| 句尾呼吸声 | 模型生成的尾音 | 裁掉最后一段安静、无周期的尾巴（最多扣住约 0.8 s） | `tts-service/tail_gate.py` |
| 句尾被掐断 | 模型在最后一个字还在发声时停下 | 引擎补 3 帧（见第 1 节） | 补丁 |
| 一句话里前响后轻 | 增益只按开头定 | 慢速 AGC：压 60 dB/s、升 6 dB/s，目标 -20 dBFS，最多 +8 / -10 dB | `standalone/agc.py`（仅 standalone） |
| 段间停顿太长 | 每段结尾都补了 0.9 s | 每句按 `chunk_pause_ms`（400 ms）向 tts-service 要停顿（请求里的 `pause`）；v0.1.3 以前是服务端扣住最后 1.2 s 再修剪，首帧因此晚 0.19 s、偶尔开头断 0.13 s | `standalone/session.py`（仅 standalone） |

句间响度只按每句开头 0.6 s 的 RMS 定增益（实测句间响度差 4.6 → 3.7 dB），不是响度标准。每一条的来由见 [踩坑 #14～#17](pitfalls.md) 和 [standalone-tuning.md](standalone-tuning.md)。

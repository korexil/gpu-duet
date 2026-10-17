# TTS 服务

在仓库根目录的 PowerShell 里执行：

```powershell
pip install -r tts-service\requirements.txt
python tts-service\server.py --library <包目录>\qwen3_tts_bridge.dll --models <模型目录>\tts `
    --icl-prompt voice\icl-prompt.json --language zh
```

DLL 会自动从 `--library` 所在目录（及其 `bin\`）和 `nvidia-cuda-runtime-cu12` / `nvidia-cublas-cu12` 两个 wheel 里找；用 CUDA Toolkit 的话加 `--dll-dir "$env:CUDA_PATH\bin"`。

| 参数 | 默认 | 说明 |
|---|---|---|
| `--library` | 必填 | `qwen3_tts_bridge.dll`（Linux 为 `.so`）的路径 |
| `--models` | 必填 | 转换好的 GGUF 目录（talker + tokenizer） |
| `--icl-prompt` | — | 克隆声音：Base 模型 + `extract_voice.py` 提取的声音提示。与 `--speaker` 二选一，必须给一个 |
| `--speaker` | — | CustomVoice 模型的预设说话人，如 `vivian`、`ryan`、`aiden`（需要转换好的 CustomVoice 模型，`setup-models.ps1` 不下载；仓库里的实测数据都是 Base + 克隆） |
| `--instruct` | — | 配合 `--speaker` 的语气指令，如「语速放慢，轻快有起伏，语气自然」 |
| `--backend` | `cuda` | `cuda` 或 `cpu` |
| `--dll-dir` | — | 额外的 DLL 搜索目录，可重复 |
| `--log` | — | 每句追加一行 JSON：接口、字数、样本数、耗时、是否取消、是否撞到 token 预算、种子 |
| `--language` | `zh` | `zh en ja ko de fr es it pt ru` |
| `--sentence-pause` | `0.9` | 每句后补齐到多少秒停顿；短句用 0.5 s。设成 0 就只保留模型自带的尾音 |
| `--clause-chars` | `6` | 多少字以内算短句（服务端已去掉标点，只能按长度分；日语、中文 6 合适，拉丁字母语言要调大） |
| `--seed` | `7` | 采样种子，同一句话每次声音完全一样；`random` 每句换一个 |
| `--queue-timeout` | `30` | 引擎忙时第二个请求最多排队几秒，超时才回 503 |
| `--port` | `18766` | |

| 接口 | 说明 |
|---|---|
| `GET /health` | `{"ready", "busy", "started", "served", "backend", "active_backend", "sentence_pause"}`，例如 `{"ready": true, "busy": false, "started": 12, "served": 12, "sentence_pause": 0.9, ...}`；`started` / `served` 单调递增，`busy` 和这两个计数供 LLM provider 轮流使用 GPU |
| `POST /stream` | `{"text": "...", "pause": 0.4}`（`text` 1～300 字，请求体 1～8192 字节；`pause` 可选，0～3 秒，这句后面留多长静音，不给用 `--sentence-pause`；≤ `--clause-chars` 字的短句取 `min(0.5, pause)`）→ chunked 原始 PCM（s16le、24 kHz、单声道，`audio/pcm`），一边合成一边返回 |
| `POST /synthesize` | 同上，但返回完整 WAV，方便试听 |
| 并发 | 一次只合成一句；第二个请求排队，`--queue-timeout`（默认 30 s）后回 503；排队中或合成中客户端断开就放弃这句 |
| 错误 | 参数不对（含 `pause` 不是 0～3 的数）400；合成失败且还没发出数据时 500 |

服务只监听 `127.0.0.1`，启动时先合成一句预热。每句的音频 token 预算按长度给（每字 8 个，最少 128、最多 1024）；真撞到预算说明句尾被截掉了，stderr 会打一行 warning，`--log` 的记录里 `truncated` 为 true。

接 xiaozhi 时 TTS 选 `index_stream`，`api_url` 填 `http://host.docker.internal:18766/stream`。

## 输出处理（`leveler.py`）

语音管线（如 xiaozhi）逐句调用 TTS。每句单独合成后直接拼接，会出现下面这些问题：

| 现象 | 原因 | 处理 |
|---|---|---|
| 一段回答里忽大忽小（句间差 5～7 dB） | 每句的音量由模型随机决定 | 每句用开头 0.6 s 的有声部分定一个增益（目标 -18 dBFS），整句保持不变 |
| 句与句之间停顿忽长忽短（0.2～1.25 s） | 模型每句自带 0.4～0.7 s 的开头静音，句尾长短不一 | 切掉开头静音，句尾统一补到固定停顿（默认 0.9 s；6 个字以内的短应答 0.5 s） |
| 小喇叭爆音、破音 | 峰值接近满幅，Opus 编解码还会再冲高一点 | 1 ms 块平滑限幅（提前看一块，约 80 ms 恢复），峰值 -6 dBFS，-3 dBFS 处再加一道硬限 |
| 每次开口第一个字前「啪」一声 | 设备空闲时会关掉功放，开口时上电 | 静默超过 1.5 s 后的第一句前留 0.3 s 静音 |

`SENTENCE_PAUSE`、`CLAUSE_PAUSE`、`LIMIT_CEILING` 等常量在 `leveler.py` 开头；目标响度（-18 dBFS）和最大增益（9 dB）是 `Leveler` 的构造参数。句尾呼吸声由 [`tail_gate.py`](tail_gate.py) 在整形之前裁掉。
xiaozhi 服务端会去掉每段末尾的标点，所以这里只能按句子长短区分停顿，看不到原来是句号还是逗号。

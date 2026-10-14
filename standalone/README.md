# 独立服务端

一个 Python 进程，直接和刷了 [xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) 固件的板子说话。不用 Docker、数据库、管理后台；ASR 和 VAD 在 CPU 上跑，LLM 和 TTS 走 `llama-server` 和本仓库的 `tts-service`。

```
ESP32（xiaozhi 固件） ──WebSocket/Opus──▶ standalone/server.py ──▶ llama-server（GPU）
                                              │  Silero VAD + SenseVoice（CPU）
                                              └──▶ tts-service（GPU）
```

和接到 xiaozhi-esp32-server 相比少了管理界面、多设备、声纹、工具调用；多了一件事：**半双工在服务端做**——自己说话时和说完之后的一小段，麦克风帧直接丢掉，不管固件的 AEC 开没开，都不会听到自己说话再接着答。

## 跑起来

前提：[主 README 快速开始](../README.md#快速开始) 的 1～4 步已经做完，`tts-service` 和 `llama-server` 在跑。

```powershell
pip install -r standalone\requirements.txt
python standalone\setup_models.py            # SenseVoice int8（约 240 MB）+ Silero VAD + GTCRN 降噪（0.5 MB），放到 standalone\models\
copy standalone\config.example.yaml standalone\config.yaml   # 按需改提示词、唤醒词、端口
python standalone\server.py
```

启动日志最后一行会打出 OTA 地址，形如 `http://192.168.1.10:8003/xiaozhi/ota/`（设备 WebSocket 在 8000，OTA 和状态页在 8003，和 xiaozhi-esp32-server 一样的分法）。打开 `http://<本机IP>:8003/` 能看到 LLM 和 TTS 是否连上。

Windows 下 Opus 编解码用的是 `pyogg` 轮子里自带的 `opus.dll`；Linux/macOS 装系统的 libopus（`apt install libopus0` / `brew install opus`）。

## 让板子连过来

xiaozhi 固件启动时先访问 OTA 地址拿 WebSocket 地址，所以只要把 OTA 指向这台电脑：

- **刷固件时**：`idf.py menuconfig` → Xiaozhi Assistant → OTA URL 填上面那个地址。
- **已经刷好的板子**：在配网页面（设备进入配网模式后连它的热点）里改 OTA 地址；或者原来接的是 xiaozhi-esp32-server，在它的管理后台把 `server.websocket` 参数改成 `ws://<本机IP>:8000/xiaozhi/v1/`，板子下次连接就会过来。

防火墙要放行 8000 和 8003 两个 TCP 端口。前面有端口转发时，用 `advertise_host` / `advertise_port` 指定 OTA 应答里写给设备的地址。

没有板子也能试：

```powershell
python standalone\loopback.py --text "今天天气怎么样"                 # 当作设备发来的文字
python standalone\loopback.py --wav question.wav --out reply.wav      # 真走 Opus + VAD + ASR
```

## 会发生什么

1. 板子唤醒后发 `listen detect`，文字匹配 `wake.words` 里的唤醒词就先应一声（`wake.ack`，启动时合成一次并缓存，不用现合成）。
2. 之后板子常开收音。VAD 判到开口就开始攒音频（前面补 `preroll_ms` 避免掉字），停顿 `end_silence_ms` 算说完，整句先过 GTCRN 降噪（`asr.denoise`，默认开；模型缺失时跳过并警告），再送 SenseVoice。
3. 识别出的文字发回板子显示（`stt`），LLM 生成回答 → 第一段过 `splitter.first_min_chars`（默认 12 字）遇到句末就切出，之后每段攒够 `splitter.min_chars`（默认 30 字，一段一两句）才交给 `tts-service` 合成一段 → 边合成边切成 Opus 每 60 ms 一帧发回，段尾 tts-service 补的静音修剪到 `chunk_pause_ms`。LLM 和 TTS 严格串行，GPU 不会被两边同时抢。段切得太碎语调接不上（每段是独立的一次合成），切得太长（70 字以上）1.7B 的模型会在中途变成一字一句地念，默认值是这两头之间的折中。
4. 说话期间和之后 `guard_ms` 内，上行音频全丢。板子按 `abort` 或再叫唤醒词可以打断。
5. `idle_close_s` 默认 0：连接一直开着、一直在听，不会断开。设成秒数（如 120）时，没人说话这么久就断开，板子回到待机等唤醒词。

固件自带回声消除（`CONFIG_USE_DEVICE_AEC`）的板子可以把 `voice.barge_in` 打开：回答播放中连续听到 `barge_in_ms` 的人声就停下来听。没有回声消除时别开，喇叭的声音会把自己打断。

## 看它在干什么

`http://<本机IP>:8003/dashboard` 是一个实时面板：问题、每 60 ms 一帧的下行音频波形、首声时刻、断音（按设备预缓冲估计，标红）、当前句字幕，右侧是内存、显存和 GPU 占用。中途打开也能补上当前这轮回答。数据来自 `/events`（server-sent events，JSON 行），拿来录演示视频或者自己画图都行。

对比用的两个开关，正常使用都不用动：`llm.overlap: true` 让 LLM 不等 TTS、一直往前写，和 TTS 同时抢 GPU，就是没有轮流调度时的样子；`dashboard_mode` 只改面板左上角的标签（`origin` / `engine` / `overlap` / `duet`），留空按 `llm.overlap` 自动判断。

## 配置

见 [`config.example.yaml`](config.example.yaml)，每一项有注释；默认值以 [`server.py`](server.py) 的 `DEFAULTS` 为准。几个常问的：

| 想要 | 改 |
|---|---|
| 换人设 | `prompt` |
| 日语 / 英语 | `asr.language`（别用 `auto`：短句会被判成别的语种，LLM 就跟着换语言）、`prompt` 里说明，`tts-service` 用对应 `--language` 启动 |
| 第一句出声更快 | `splitter.first_min_chars` 调小（第一段过这么多字遇句末就切），或 `first_cut_chars` 设成 8 左右（首段在逗号处切出）；代价是段与段之间语调割裂、停顿明显 |
| 说完太久才开始听 | `voice.guard_ms` 调小；板子喇叭余音长就调大 |
| 一句话被切成两段 | `voice.end_silence_ms` 调大 |
| 多块板子 | 直接连，每条连接各自有对话历史；LLM 和 TTS 排队 |
| 听听不轮流是什么样 | `llm.overlap: true`，面板标签会变成「对照」 |

主要配置项（其余见 `config.example.yaml` 和 `DEFAULTS`）：

| 项 | 默认 | 作用 |
|---|---|---|
| `listen.port` / `listen.http_port` | 8000 / 8003 | WebSocket / HTTP（OTA、状态、面板） |
| `advertise_host` / `advertise_port` | 空 / 0 | 写进 OTA 应答的地址，前面有端口转发时用；空 / 0 = 设备请求时的 Host / `listen.port` |
| `token` | 空 | 非空时设备必须带 `Authorization: Bearer <token>` |
| `asr.language` | `zh` | 别用 `auto`：短句（如唤醒词）会被判成别的语种 |
| `asr.denoise` | `true` | 送识别前整句过 GTCRN（模型缺失时警告并跳过） |
| `splitter.first_min_chars` / `min_chars` / `max_chars` | 12 / 30 / 70 | 第一段过 12 字遇句末就切；之后攒够 30 字才在句末切；超过 70 字在逗号处切；引号、括号内不切 |
| `splitter.first_cut_chars` | 0 | >0 时第一段最早在这么多字后的逗号处切，首声更快、句子割裂 |
| `voice.end_silence_ms` / `min_voice_ms` / `preroll_ms` / `max_utterance_s` | 700 / 250 / 600 / 20 | 判停、最短语音、句首前摇、单句上限 |
| `voice.guard_ms` | 500 | 说完后继续丢弃麦克风帧的时长（这段帧进前摇缓冲） |
| `voice.barge_in` / `barge_in_ms` | `false` / 600 | 固件有回声消除时才开：说话中听到连续人声就停 |
| `voice.chunk_pause_ms` / `tail_hold_ms` | 400 / 1200 | 段尾静音修剪到的长度 / 为修剪而扣住的尾部 |
| `voice.agc.*` | 见 [how-it-works.md](../docs/how-it-works.md#3-逐句整形统一响度停顿和峰值) | 下行慢速 AGC（压 60 dB/s、升 6 dB/s，目标 -20 dBFS，最多 +8 / -10 dB），`enabled: false` 关闭 |
| `wake.words` / `wake.ack` | `你好小智` 等 / `嗯？` | 唤醒词与应答 |
| `wake.device_text` | `chat` | 设备以文字发来的非唤醒词（`listen detect`）：`chat` 当作提问，`ignore` 丢掉 |
| `idle_close_s` | 0 | 0 = 一直连着、一直听；>0 = 没人说话这么多秒就断开，板子回待机 |
| `llm.max_tokens` / `turn_taking` / `overlap` | 1500 / `false` / `false` | `overlap: true` 让 LLM 不等 TTS 一直往前写，用来听没有轮流时的样子 |
| `dashboard_mode` / `dashboard_title` | 空 | 只改面板标签（`origin` / `engine` / `overlap` / `duet`），留空按 `llm.overlap` 判断 |
| `prompt` / `history_turns` | 见示例 / 10 | 系统提示词 / 保留的对话轮数 |

## HTTP 端点（8003）

| 端点 | 说明 |
|---|---|
| `/xiaozhi/ota/` | GET / POST，返回 WebSocket 地址 |
| `/`、`/health`、`/status` | LLM、TTS 是否在线，会话数 |
| `/dashboard` | 实时面板：问题、每帧下行电平、首声、断音、当前句、内存 / 显存 / GPU 占用 |
| `/events` | SSE，JSON 行；中途打开会先补发当前这轮 |

WebSocket 端口（8000）上不带 Upgrade 的 GET 也能回 OTA 和状态。`-v` 时每 5 秒记一行上行音频统计（帧数、有声帧、峰值、VAD 最高概率、底噪主频）。

## 没做的

- 工具调用 / MCP：板子发来的 `mcp` 消息忽略（音量等设备侧工具要接 xiaozhi-esp32-server 的补丁）。
- 声纹、记忆、意图识别、固件升级分发（OTA 应答里 `firmware.url` 为空）。
- 流式 ASR：整句说完才识别，SenseVoice 一句 0.3 s 左右。

## 测试

`python -m pytest tests/test_standalone.py`：假设备走真 WebSocket，VAD/ASR/LLM/TTS 用替身，不需要模型和显卡。

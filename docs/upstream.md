# 上游 PR 和实验分支

## 提给上游的改动

截至 2026-10-14：xiaozhi-esp32-server 已合并 2 个、待合并 1 个；qwen3-tts.cpp 2 个待回复、3 个分支未提。

| 上游 | PR / 分支 | 内容 | 状态 |
|---|---|---|---|
| xiaozhi-esp32-server | [#3411](https://github.com/xinnan-tech/xiaozhi-esp32-server/pull/3411) | FunASR 的 JSON 原样进入对话历史 | 已合并（10-13） |
| xiaozhi-esp32-server | [#3412](https://github.com/xinnan-tech/xiaozhi-esp32-server/pull/3412) | SenseVoice 日语识别结果按词带空格 | 待合并 |
| xiaozhi-esp32-server | [#3413](https://github.com/xinnan-tech/xiaozhi-esp32-server/pull/3413) | TTS 分句在引号内切开句子 | 已合并（10-12） |
| qwen3-tts.cpp | [#5](https://github.com/Danmoreng/qwen3-tts.cpp/pull/5)（`utf8-paths`） | 模型、声音提示、参考音路径按 UTF-8 打开 | 待回复 |
| qwen3-tts.cpp | [#6](https://github.com/Danmoreng/qwen3-tts.cpp/pull/6)（`c-bridge-guards`） | C 边界捕获异常、导出结构体 ABI 表、不依赖 JNI 的 C 桥接库选项 | 待回复 |
| qwen3-tts.cpp | `qkv-dedup` | Q/K/V 去重（GPU −488 MiB） | 分支已备好，未提 |
| qwen3-tts.cpp | `text-embd-cpu`（基于 `qkv-dedup`） | 文本表放 CPU + 只读映射（GPU、内存各 −594 MiB） | 分支已备好，未提 |
| qwen3-tts.cpp | `icl-prompt-json-unicode` | 读 ICL 提示 JSON 时解码 `\uXXXX`（含代理对） | 分支已备好，未提 |

后三个等 #5、#6 有回应再提。分支在 [korexil/qwen3-tts.cpp](https://github.com/korexil/qwen3-tts.cpp)；上游 `main` 仍是 `16bb5af`。

本仓库补丁里的尾音补帧（`QWEN3_TTS_TAIL_PAD_FRAMES`）和解码器按需批处理没有对应的上游分支。

## ICL 提示 JSON 里的 `\uXXXX`

上游读 ICL 提示 JSON 时不解转义，用标准 JSON 库默认参数（如 Python `json.dumps`）写出的提示，非 ASCII 文字会被 talker 当成 `u7684` 这样的字面串念出来。本仓库 `native-tts` 补丁**还没有**这项修复；按快速开始用 `extract_voice.py` 生成的提示由 `qwen3-tts-cli` 写出，非 ASCII 原样写入，不受影响。自己用 Python 写提示时加 `ensure_ascii=False`。

## 实验中：`codes-continuation` 分支（本地实验，未公开、未合入）

目标是让同一段回答里各句的语调连贯。主分支不含以下改动：

- C 桥接新接口 `qwen3_tts_get_last_codes`：取上一次合成时 talker 生成的 codes。
- tts-service：`/stream` 和 `/synthesize` 可选字段 `"reply": "<id>"`（同一 id 的句子续写合成：上一句末尾约 2.5 s 的小句及其 codes 接在参考音后面，`--context-seconds` 调，0 = 整句）和 `"voice": "<名>"`（从 `--voice-dir` 选另一份声音提示，如同一人的不同情绪）；新增 `--temperature`（默认 0.9）。
- 该分支的补丁带上了 ICL 提示 JSON 的 `\uXXXX` 解码（见上节），Python 侧写提示也改用 `ensure_ascii=False`。
- 目前的结论（实验记录不在仓库里）：把上一句整句作为上下文续写时，语速会一句比一句快；只带上一句末尾约 2.5 s 的小句时语速最稳；采样温度 0.9 和 0.7 盲听分不出可靠差别。

# xiaozhi-esp32-server 补丁

`xiaozhi-esp32-server.patch` 基于 [xinnan-tech/xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server) `a6cd755`（见 `PATCH_BASE.txt`）：

```bash
git clone https://github.com/xinnan-tech/xiaozhi-esp32-server.git
cd xiaozhi-esp32-server && git checkout a6cd755
git apply /path/to/xiaozhi-esp32-server.patch
```

另有三个通用修复已单独提交给上游：[#3411](https://github.com/xinnan-tech/xiaozhi-esp32-server/pull/3411)（FunASR 的 JSON 原样进入对话历史）、[#3412](https://github.com/xinnan-tech/xiaozhi-esp32-server/pull/3412)（SenseVoice 日语识别结果按词带空格）、[#3413](https://github.com/xinnan-tech/xiaozhi-esp32-server/pull/3413)（TTS 分句在引号内切开句子）。未合并前建议一起打上。

## 包含的内容

| 文件 | 作用 |
|---|---|
| `core/providers/llm/llamacpp_paced/` | 新的 LLM provider：直接调用 llama-server 的 `/completion`，按 token 精确地分块续写；每说完一句，就等 TTS 的 `/health` 不再 `busy` 才继续，让 LLM 和 TTS 轮流用同一块 GPU |
| `core/handle/wakeGate.py` | 服务端唤醒闸门：板子保持一个常开的收音会话，由服务端 ASR 判断有没有叫名字；回应后一段时间内不用再叫名字；窗口结束后道别一次，然后回到等待名字的状态（连接不断开） |
| `receiveAudioHandle.py` 等 4 处接入 | 闸门、道别、窗口延长、设备音量（MCP 工具就绪后，按 `data/device_volume.json` 设置板子喇叭音量） |

## 配置

- LLM：参考 `config-examples/llm-llamacpp_paced.json`。用管理后台的话，参考 `register-llm.sql`。
- 唤醒闸门：把 `config-examples/wake_gate.json` 放到服务端的 `data/` 下。`wakeup_words`（系统参数）总是会被识别；`wake_patterns` 写额外的正则，用来匹配同音字和常见识别错误。比如一个日语名字，SenseVoice 经常把它识别成同音的汉字，就要把那个汉字写法也加进来。
- 设备音量：`data/device_volume.json`，`{"volume": 0-100}`，设备每次连接时生效。

闸门开启时，「无声音超时断开」不再生效（会话需要一直开着），超时只会触发道别。

## 测试

```bash
pip install pytest pytest-asyncio
python -m pytest tests/core/handle/test_wake_gate.py tests/core/providers/llm/test_llamacpp_paced.py
```

上游的 `tests/conftest.py` 会把 `httpx.Client` 替换成离线的假实现，所以 provider 的测试改用 `httpx._client.Client` 加 `MockTransport`。

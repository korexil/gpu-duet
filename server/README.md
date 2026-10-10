# 接入 xiaozhi-esp32-server

两种接法：只要 GPU 轮流的话，**不用打补丁、不用重建镜像**；要服务端唤醒和设备音量，再打补丁。

## 最小接入：挂载一个文件夹

xiaozhi 按配置里的 `type` 去 `core/providers/llm/<type>/` 找 provider，所以把 `llamacpp_paced` 挂进官方镜像就能用。

1. 把 [`config-examples/docker-compose.override.yml`](config-examples/docker-compose.override.yml) 放到 xiaozhi 的 `docker-compose.yml` 旁边。`GPU_DUET` 指向本仓库，默认是同级的 `gpu-duet` 文件夹。
2. 把 [`config-examples/data-config.yaml`](config-examples/data-config.yaml) 的内容合并进 `data/.config.yaml`。用管理后台（manager-api）的话，LLM 用 [`register-llm.sql`](config-examples/register-llm.sql) 注册，TTS 在后台选 `index_stream`，地址填 `http://host.docker.internal:18766/stream`。
3. `docker compose up -d`。

删掉 `voice_health_url` 就是不轮流的普通流式 provider，方便对照。

## 完整补丁：服务端唤醒 + 设备音量

`xiaozhi-esp32-server.patch` 基于 [xinnan-tech/xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server) `a6cd755`（见 `PATCH_BASE.txt`），需要从源码构建镜像：

```bash
git clone https://github.com/xinnan-tech/xiaozhi-esp32-server.git
cd xiaozhi-esp32-server && git checkout a6cd755
git apply /path/to/xiaozhi-esp32-server.patch
```

| 文件 | 作用 |
|---|---|
| `core/providers/llm/llamacpp_paced/` | 同上，GPU 轮流的 LLM provider |
| `core/handle/wakeGate.py` | 服务端唤醒闸门：板子保持常开收音，服务端 ASR 听到名字才回应；回应后一段时间内不用再叫名字；窗口结束道别一次，回到等待名字（连接不断开） |
| `receiveAudioHandle.py` 等 4 处接入 | 闸门、道别、窗口延长、设备音量（MCP 工具就绪后按 `data/device_volume.json` 设置喇叭音量） |

- 唤醒闸门：把 `config-examples/wake_gate.json` 放到 `data/` 下。`wakeup_words`（系统参数）总是会被识别；`wake_patterns` 写额外的正则，匹配同音字和常见识别错误。比如日语名字常被 SenseVoice 识别成同音汉字，就把汉字写法也加进来。
- 设备音量：`data/device_volume.json`，`{"volume": 0-100}`，设备每次连接时生效。
- 闸门开启时，「无声音超时断开」不再生效（会话需要一直开着），超时只触发道别。

另有三个通用修复已单独提交给上游：[#3411](https://github.com/xinnan-tech/xiaozhi-esp32-server/pull/3411)（FunASR 的 JSON 原样进入对话历史）、[#3412](https://github.com/xinnan-tech/xiaozhi-esp32-server/pull/3412)（SenseVoice 日语识别结果按词带空格）、[#3413](https://github.com/xinnan-tech/xiaozhi-esp32-server/pull/3413)（TTS 分句在引号内切开句子）。未合并前建议一起打上。

## 测试

```bash
pip install pytest pytest-asyncio
python -m pytest tests/core/handle/test_wake_gate.py tests/core/providers/llm/test_llamacpp_paced.py
```

上游的 `tests/conftest.py` 会把 `httpx.Client` 替换成离线的假实现，所以 provider 的测试改用 `httpx._client.Client` 加 `MockTransport`。

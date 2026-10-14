# 开发：目录、测试、CI

## 目录结构

```
bench/        GPU 轮流 × 句子长短 benchmark：run.py、原始结果 results/
docs/         原理、架构、实测、上游、踩坑、调试记录、参考音、开发 · img/（图，make_figures.py 生成）
native-tts/   qwen3-tts.cpp 补丁、Windows CUDA 编译 / 打包（build.ps1）、模型下载转换（setup-models.ps1）
tts-service/  HTTP TTS 服务（server.py）、ctypes 绑定（native_engine.py）、整形（leveler.py、tail_gate.py）、声音提示提取
scripts/      start-llm.ps1（llama-server 参数）
standalone/   独立服务端：协议、VAD / ASR / 降噪、分句、半双工、AGC、实时面板、loopback 客户端、模型下载
server/       xiaozhi-esp32-server 用的 provider、wakeGate、补丁（基线 a6cd755）、配置示例、sync_patch.py
tests/        pytest
tools/        turn_taking_sim · agc_check（AGC 前后响度起伏）· device_probe（软件设备测 xiaozhi）· loudness_stats · wake_test（唤醒命中计数）· esp32/xiaozhi_assets（assets 分区）
```

## 测试

不需要显卡、模型或 xiaozhi：

```powershell
pip install -r tts-service\requirements.txt -r standalone\requirements.txt pytest
python -m pytest tests
```

覆盖：

- leveler、tail gate
- tts-service：第二个请求排队而不是 503、排队中断开的请求不合成、`started` / `served` 计数
- `llamacpp_paced`（假的 llama-server 和 TTS）、wakeGate
- standalone：假设备走真 WebSocket，ASR→LLM→TTS 一轮、半双工、唤醒应答和设备文字、abort、OTA / 状态、分句、配置合并、Opus 往返、AGC、段尾修剪
- ABI 布局与类型码
- xiaozhi 补丁里内嵌的文件和仓库一致（改过 `llamacpp_paced.py`、`wakeGate.py` 后跑 `python server/sync_patch.py`，见 [server/README.md](../server/README.md)）

## CI

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml)：

- Python 3.10 / 3.12 跑测试。
- 在 Linux 和 Windows 上把补丁打到上游 `16bb5af`、编 CPU 版桥接库并逐字段核对 ABI。
- 检查 xiaozhi 补丁能否打到基线 `a6cd755`（另对上游 `main` 试一次，只报告）。

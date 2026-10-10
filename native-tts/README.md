# 原生 Qwen3-TTS 1.7B（F16，C++/ggml，CUDA）

上游：[Danmoreng/qwen3-tts.cpp](https://github.com/Danmoreng/qwen3-tts.cpp)（MIT）@ `16bb5af`。
`qwen3-tts-cpp-16bb5af.patch` 在它之上增加：

| 改动 | 开关（环境变量） | 实测收益（RTX 4060 Laptop 8 GB） |
|---|---|---|
| C ABI 桥接库 `qwen3_tts_bridge`，流式回调输出 PCM，`qwen3_tts_bridge_abi_sizes` 校验结构体 | CMake `-DQWEN3_TTS_BUILD_C_BRIDGE=ON` | 供 Python/ctypes 直接调用 |
| Talker Q/K/V 只保留融合后的一份 | `QWEN3_TTS_QKV_DEDUP=1` | GPU −约 488 MiB |
| 文本 embedding 表留在 CPU，只在 CPU 上做 get_rows | `QWEN3_TTS_TEXT_EMBD_CPU=1` | GPU −约 594 MiB |
| 上面的 CPU 表改为只读内存映射（Windows，按行范围映射，不预读、不复制整表） | `QWEN3_TTS_TEXT_EMBD_MMAP=1` | RSS −约 594 MiB |
| 解码器按需批处理 | `QWEN3_TTS_LAZY_BATCH_DECODER=1` | — |

全部打开时，常驻显存约 3.7～3.9 GB。在测试过的种子、文本和参数下，输出 PCM 与关闭这些开关时**逐字节相同**；这只说明测过的配置等价，不保证所有输入。
`tts-service/server.py` 启动时会默认打开这些开关。

**复现性验证：** 用 `build.ps1` 从干净的上游克隆开始编译（CUDA 12.8、`-CudaArch 89`），得到的 DLL 和开发时用的 DLL，在同样的模型、声音提示和文本下，`tts-service` 输出逐字节相同（5/5 句），RTF 0.58～0.61。

## 1. 编译（Windows）

需要：VS 2022 Build Tools（C++ 工作负载，自带 CMake 和 Ninja）、git、驱动支持的 CUDA Toolkit。

```powershell
.\build.ps1 -CudaRoot "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8" -CudaArch 89
```

`-CudaArch`：RTX 40 系 = 89，RTX 30 系 = 86。工作目录要用**短路径**（比如 `-WorkDir C:\qtts`）。nvcc 的中间文件嵌套很深，路径一长就会超过 Windows 260 字符的限制，报错 `Could not open output file ...cu.obj.d`。产物在 `<WorkDir>\build-cuda\`：`qwen3_tts_bridge.dll`、`qwen3-tts-cli.exe` 和 ggml 的 DLL。

## 2. 转换模型（F16 GGUF）

从 Hugging Face 下载 `Qwen/Qwen3-TTS-12Hz-1.7B-Base`，用上游自带的脚本转换：

```bash
python scripts/convert_tts_to_gguf.py       --input <snapshot> --output models-f16/qwen-talker-1.7b-base-F16.gguf --type f16
python scripts/convert_tokenizer_to_gguf.py --input <snapshot> --output models-f16/qwen-tokenizer-12hz-F16.gguf --type f16
```

参数名以上游脚本的 `--help` 为准。Q8 talker 显存更省，但听感明显更平淡，这里用 F16。

## 3. 用你自己的参考音提取「声音提示」

准备一段 3～10 秒、干净、说话风格就是你想要的参考音，加上它的逐字文本：

```powershell
work\build-cuda\qwen3-tts-cli.exe -m models-f16 --backend cpu -j 4 `
    -r my_voice.wav --reference-text-file my_voice.txt --extract-icl-prompt voice\icl-prompt.json
```

语气、语速和句尾习惯主要从参考音里学：参考音偏快，合成就偏快。

## 4. 启动服务

见 `tts-service/README.md`。

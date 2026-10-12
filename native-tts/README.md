# 原生 Qwen3-TTS 1.7B（F16，C++/ggml，CUDA）

上游：[Danmoreng/qwen3-tts.cpp](https://github.com/Danmoreng/qwen3-tts.cpp)（MIT）@ `16bb5af`。
`qwen3-tts-cpp-16bb5af.patch` 在它之上增加：

| 改动 | 开关（环境变量） | 实测收益（RTX 4060 Laptop 8 GB） |
|---|---|---|
| C ABI 桥接库 `qwen3_tts_bridge`，流式回调输出 PCM；`qwen3_tts_bridge_abi_layout` 导出四个结构体的大小和每个字段的偏移，绑定加载时逐字段核对；所有导出函数在 C 边界捕获 C++ 异常，返回错误而不是让宿主进程崩溃 | CMake `-DQWEN3_TTS_BUILD_C_BRIDGE=ON` | 供 Python/ctypes 直接调用 |
| Talker Q/K/V 只保留融合后的一份 | `QWEN3_TTS_QKV_DEDUP=1` | GPU −约 488 MiB |
| 文本 embedding 表留在 CPU，只在 CPU 上做 get_rows | `QWEN3_TTS_TEXT_EMBD_CPU=1` | GPU −约 594 MiB |
| 上面的 CPU 表改为只读内存映射（Windows，按行范围映射，不预读、不复制整表） | `QWEN3_TTS_TEXT_EMBD_MMAP=1` | RSS −约 594 MiB |
| 解码器按需批处理 | `QWEN3_TTS_LAZY_BATCH_DECODER=1` | — |

全部打开时，常驻显存约 3.7～3.9 GB。在测试过的种子、文本和参数下，输出 PCM 与关闭这些开关时**逐字节相同**；这只说明测过的配置等价，不保证所有输入。
`tts-service/server.py` 启动时会默认打开这些开关。所有开关的取值规则一样：`1`、`true`、`on`、`yes`（不分大小写）算开，其他都算关。

模型路径含非 ASCII 字符（日文用户目录很常见）时，只读映射这一项会自动关掉、改成把表读进内存（多占约 0.6 GB 内存，PCM 不变），stderr 里会有一行提示；原因是上游加载其余张量仍用 ANSI `fopen`，同一个路径按两种编码打开可能不是同一个文件。

`qwen3_tts_bridge_abi_sizes` 仍然保留；只有它、没有 `qwen3_tts_bridge_abi_layout` 的旧库也能加载，但绑定会打一条「字段布局未校验」的警告。

## 1. 引擎：下载预编译包（Windows x64）

从 [Releases](https://github.com/korexil/gpu-duet/releases/latest) 下载 `gpu-duet-tts-win-x64-cuda12-sm75-86-89-120.zip`，解压到任意目录。编进了 RTX 20 / 30 / 40 / 50 系（sm 75 / 86 / 89 / 120），实机只测过 RTX 40 系（sm 89）。

每个 Release 的说明里都写了 zip 的 SHA256，下载后对一下（`Get-FileHash <zip>`）。包是用 `build.ps1` 从干净的上游克隆加本仓库的补丁编出来的，但这不是可复现构建：自己编出的 DLL 二进制不会和包里的逐字节相同，只能比合成结果（实测同模型、同种子、同文本下 PCM 逐字节相同）。信不过预编译包就自己编，见下一节。

CUDA 运行库用 NVIDIA 的官方 wheel，不用装 CUDA Toolkit：

```powershell
pip install nvidia-cuda-runtime-cu12 nvidia-cublas-cu12
```

`tts-service` 会自动在包所在目录和这两个 wheel 里找 DLL。另外需要 [VC++ 运行库](https://aka.ms/vs/17/release/vc_redist.x64.exe)（大多数机器已经装了）。包是用 CUDA 12.8 编译的，实测驱动 591.59。

在没装 CUDA Toolkit、PATH 里没有任何 CUDA 目录的环境里验证过：DLL 全部从包目录和这两个 wheel 加载，RTF 0.62～0.70；合成出的音频、提取出的声音提示，都和自己编译的版本逐字节相同。

### 或者自己编译

需要：VS 2022 Build Tools（C++ 工作负载，自带 CMake 和 Ninja）、git、驱动支持的 CUDA Toolkit。

```powershell
.\build.ps1 -CudaRoot "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8" -CudaArch 89 -WorkDir C:\qtts
```

`-CudaArch`：RTX 40 系 = 89，RTX 30 系 = 86，可以写多个（`"86;89"`）。工作目录要用**短路径**：nvcc 的中间文件嵌套很深，路径一长就会超过 Windows 260 字符的限制，报错 `Could not open output file ...cu.obj.d`。加 `-Package` 会打出和 Release 一样的 zip。

用 `build.ps1` 从干净的上游克隆编译出的 DLL，和开发时用的 DLL，在同样的模型、声音提示和文本下输出逐字节相同（5/5 句），RTF 0.58～0.61。

## 2. 模型

```powershell
.\setup-models.ps1 -Out D:\models -InstallDeps
```

- **TTS**：下载 `Qwen/Qwen3-TTS-12Hz-1.7B-Base`，用上游的转换脚本转成 F16 GGUF（talker + tokenizer 两个文件，约 4.2 GB）。转换只要 CPU 版 torch，`-InstallDeps` 会装。Q8 talker 显存更省，但听感明显更平淡，这里用 F16。在新建的 venv 里从零跑过一遍，得到的两个 GGUF 和本仓库测试用的逐字节相同。venv 和输出目录别放太深，torch 里有的文件路径很长，会撞 Windows 260 字符的限制。
- **LLM**：下载 `xbill9/gemma-4-E4B-it-qat-q4_0-exact-gguf`（4.2 GB，非官方，逐位保留 Google 的 QAT 权重）。本仓库的数据都是用它测的；Google 官方的 `gemma-4-E4B-it-qat-q4_0-gguf`（5.2 GB）应该也能用，但没测过。不要 LLM 就加 `-SkipLlm`。

## 3. 用你自己的参考音提取「声音提示」

准备一段 3～10 秒、干净、说话风格就是你想要的参考音，加上它的逐字文本：

```powershell
python ..\tts-service\extract_voice.py --library <包目录>\qwen3_tts_bridge.dll --models D:\models\tts `
    --wav my_voice.wav --text my_voice.txt --out voice\icl-prompt.json
```

语气、语速和句尾习惯主要从参考音里学：参考音偏快，合成就偏快。

## 4. 启动服务

见 `tts-service/README.md`。

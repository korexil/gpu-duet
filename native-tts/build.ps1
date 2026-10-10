<#
Build the patched qwen3-tts.cpp C bridge (qwen3_tts_bridge.dll) and CLI with CUDA on Windows.

  .\build.ps1 -CudaRoot "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8" -CudaArch 89

Requires Visual Studio 2022 Build Tools (C++ workload; it ships CMake and Ninja), git and a
CUDA toolkit that your driver supports. CudaArch: 89 = RTX 40 series, 86 = RTX 30, 120 = RTX 50.
#>
param(
    [Parameter(Mandatory = $true)][string]$CudaRoot,
    [string]$CudaArch = '89',
    [string]$VsRoot = 'C:\Program Files\Microsoft Visual Studio\2022\BuildTools',
    [string]$WorkDir = (Join-Path $PSScriptRoot 'work'),
    [int]$Jobs = 4
)
$ErrorActionPreference = 'Stop'
# nvcc writes deeply nested intermediate files; past ~60 characters of WorkDir the
# Windows MAX_PATH limit makes it fail with "Could not open output file ...cu.obj.d".
if ($WorkDir.Length -gt 60) { throw "WorkDir is too long for nvcc on Windows ($($WorkDir.Length) chars). Use a short path, e.g. -WorkDir C:\qtts" }
$upstream = 'https://github.com/Danmoreng/qwen3-tts.cpp.git'
$commit = '16bb5af'
$patch = Join-Path $PSScriptRoot 'qwen3-tts-cpp-16bb5af.patch'
$source = Join-Path $WorkDir 'qwen3-tts.cpp'
$build = Join-Path $WorkDir 'build-cuda'

if (-not (Test-Path $source)) {
    git clone --recursive $upstream $source
    git -C $source checkout $commit
    git -C $source submodule update --init --recursive
    git -C $source apply --whitespace=nowarn $patch
}

$vcvars = Join-Path $VsRoot 'VC\Auxiliary\Build\vcvars64.bat'
foreach ($line in (cmd /s /c "`"$vcvars`" > nul && set")) {
    if ($line -match '^(.*?)=(.*)$') { Set-Item -LiteralPath "Env:$($matches[1])" -Value $matches[2] }
}
$cmakeDir = Join-Path $VsRoot 'Common7\IDE\CommonExtensions\Microsoft\CMake'
$env:PATH = "$CudaRoot\bin;$cmakeDir\CMake\bin;$cmakeDir\Ninja;$env:PATH"
$env:CUDA_PATH = $CudaRoot
$env:CUDACXX = Join-Path $CudaRoot 'bin\nvcc.exe'

cmake -S $source -B $build -G Ninja -DCMAKE_BUILD_TYPE=Release `
    "-DCMAKE_CUDA_ARCHITECTURES=$CudaArch" `
    -DQWEN3_TTS_CUDA=ON -DGGML_CUDA=ON -DGGML_CUDA_GRAPHS=ON `
    -DQWEN3_TTS_BUILD_C_BRIDGE=ON -DQWEN3_TTS_BUILD_SHARED=OFF -DQWEN3_TTS_COREML=OFF
if ($LASTEXITCODE -ne 0) { throw 'CMake configure failed' }
cmake --build $build --target qwen3_tts_bridge qwen3-tts-cli --parallel $Jobs
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }

Get-ChildItem $build -Recurse -Include qwen3_tts_bridge.dll, qwen3-tts-cli.exe, ggml*.dll |
    Select-Object FullName, Length | Format-Table -AutoSize
Write-Host "Done. Pass the folder containing qwen3_tts_bridge.dll and the ggml DLLs to tts-service/server.py --dll-dir."

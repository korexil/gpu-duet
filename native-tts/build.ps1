<#
Build the patched qwen3-tts.cpp C bridge (qwen3_tts_bridge.dll) and CLI with CUDA on Windows.

  .\build.ps1 -CudaRoot "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8" -CudaArch 89

Requires Visual Studio 2022 Build Tools (C++ workload; it ships CMake and Ninja), git and a
CUDA toolkit that your driver supports. CudaArch: 89 = RTX 40 series, 86 = RTX 30, 120 = RTX 50;
several at once as "75;86;89;120". -Package also writes the release zip (flat folder: bridge DLL,
CLI, ggml DLLs, licenses).
#>
param(
    [Parameter(Mandatory = $true)][string]$CudaRoot,
    [string]$CudaArch = '89',
    [string]$VsRoot = 'C:\Program Files\Microsoft Visual Studio\2022\BuildTools',
    [string]$WorkDir = (Join-Path $PSScriptRoot 'work'),
    [int]$Jobs = 4,
    [switch]$Package
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

# git, cmake and nvcc print progress on stderr; under 'Stop', Windows PowerShell 5.1 turns
# that into a terminating error as soon as stderr is redirected. Judge by the exit code.
function Invoke-Native([string]$what, [scriptblock]$block) {
    $ErrorActionPreference = 'Continue'
    & $block 2>&1 | ForEach-Object { if ($_ -is [System.Management.Automation.ErrorRecord]) { [string]$_.TargetObject } else { $_ } }
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" }
}

if (-not (Test-Path (Join-Path $source '.patched'))) {
    Remove-Item -Recurse -Force $source -ErrorAction SilentlyContinue
    Invoke-Native 'git clone' { git clone --recursive $upstream $source }
    Invoke-Native 'git checkout' { git -C $source checkout $commit }
    Invoke-Native 'git submodule' { git -C $source submodule update --init --recursive }
    Invoke-Native 'git apply' { git -C $source apply --whitespace=nowarn $patch }
    New-Item -ItemType File (Join-Path $source '.patched') | Out-Null
}

$vcvars = Join-Path $VsRoot 'VC\Auxiliary\Build\vcvars64.bat'
$ErrorActionPreference = 'Continue'   # vcvars complains on stderr when vswhere.exe is not on PATH
$vcenv = cmd /s /c "`"$vcvars`" > nul 2> nul && set"
$ErrorActionPreference = 'Stop'
foreach ($line in $vcenv) {
    if ($line -match '^(.*?)=(.*)$') { Set-Item -LiteralPath "Env:$($matches[1])" -Value $matches[2] }
}
$cmakeDir = Join-Path $VsRoot 'Common7\IDE\CommonExtensions\Microsoft\CMake'
$env:PATH = "$CudaRoot\bin;$cmakeDir\CMake\bin;$cmakeDir\Ninja;$env:PATH"
$env:CUDA_PATH = $CudaRoot
$env:CUDACXX = Join-Path $CudaRoot 'bin\nvcc.exe'

Invoke-Native 'CMake configure' {
    cmake -S $source -B $build -G Ninja -DCMAKE_BUILD_TYPE=Release `
        "-DCMAKE_CUDA_ARCHITECTURES=$CudaArch" `
        -DQWEN3_TTS_CUDA=ON -DGGML_CUDA=ON -DGGML_CUDA_GRAPHS=ON `
        -DQWEN3_TTS_BUILD_C_BRIDGE=ON -DQWEN3_TTS_BUILD_SHARED=OFF -DQWEN3_TTS_COREML=OFF
}
Invoke-Native 'Build' { cmake --build $build --target qwen3_tts_bridge qwen3-tts-cli --parallel $Jobs }

Get-ChildItem $build -Recurse -Include qwen3_tts_bridge.dll, qwen3-tts-cli.exe, ggml*.dll |
    Select-Object FullName, Length | Format-Table -AutoSize
Write-Host "Done. Pass $(Join-Path $build 'qwen3_tts_bridge.dll') to tts-service/server.py --library."

if ($Package) {
    $name = 'gpu-duet-tts-win-x64-cuda12-sm' + ($CudaArch -replace ';', '-')
    $stage = Join-Path $WorkDir $name
    Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $stage | Out-Null
    Copy-Item (Join-Path $build 'qwen3_tts_bridge.dll'), (Join-Path $build 'qwen3-tts-cli.exe') $stage
    Copy-Item (Join-Path $build 'bin\ggml*.dll') $stage
    Copy-Item (Join-Path $source 'LICENSE') (Join-Path $stage 'LICENSE-qwen3-tts.cpp.txt')
    Copy-Item (Join-Path $source 'ggml\LICENSE') (Join-Path $stage 'LICENSE-ggml.txt')
    Copy-Item (Join-Path $PSScriptRoot '..\LICENSE') (Join-Path $stage 'LICENSE-gpu-duet.txt')
    Copy-Item (Join-Path $PSScriptRoot '..\NOTICE') (Join-Path $stage 'NOTICE.txt')
    $zip = Join-Path $WorkDir "$name.zip"
    Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -Force
    Get-Item $zip | Select-Object FullName, Length | Format-Table -AutoSize
}

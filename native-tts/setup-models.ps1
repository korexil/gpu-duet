<#
Download and convert the models gpu-duet was measured with.

  .\setup-models.ps1                       # TTS (F16 GGUF) + LLM into .\models
  .\setup-models.ps1 -Out D:\models -SkipLlm

TTS: Qwen/Qwen3-TTS-12Hz-1.7B-Base from Hugging Face, converted to F16 GGUF with the
     converters of qwen3-tts.cpp @ 16bb5af (cloned without submodules, scripts only).
     Needs ~9 GB free disk during conversion (4.5 GB download + 4.2 GB output); the result is ~4.2 GB.
LLM: gemma-4-E4B-it-q4_0-exact.gguf (4.2 GB), an unofficial Q4_0 GGUF that keeps
     Google's QAT weights exactly. Google's own gemma-4-E4B-it-qat-q4_0-gguf (5.2 GB)
     should also work but was not measured here.

Python packages (CPU-only torch is enough): torch safetensors numpy tqdm gguf huggingface_hub
#>
param(
    [string]$Out = (Join-Path $PSScriptRoot 'models'),
    [string]$Python = 'python',
    [switch]$SkipLlm,
    [switch]$InstallDeps
)
$ErrorActionPreference = 'Stop'
$upstream = 'https://github.com/Danmoreng/qwen3-tts.cpp.git'
$commit = '16bb5afcd06311031c72a8488f8d59660dc2fb46'
$ttsRepo = 'Qwen/Qwen3-TTS-12Hz-1.7B-Base'
$llmRepo = 'xbill9/gemma-4-E4B-it-qat-q4_0-exact-gguf'
$llmFile = 'gemma-4-E4B-it-q4_0-exact.gguf'

# git, pip and the downloaders print progress on stderr; under 'Stop', Windows PowerShell 5.1
# turns that into a terminating error once stderr is redirected. Judge by the exit code.
function Invoke-Checked([string]$what, [scriptblock]$block) {
    $ErrorActionPreference = 'Continue'
    & $block 2>&1 | ForEach-Object { if ($_ -is [System.Management.Automation.ErrorRecord]) { [string]$_.TargetObject } else { $_ } }
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" }
}

if ($InstallDeps) {
    Invoke-Checked 'pip install torch (CPU)' { & $Python -m pip install torch --index-url https://download.pytorch.org/whl/cpu }
    Invoke-Checked 'pip install' { & $Python -m pip install safetensors numpy tqdm gguf huggingface_hub }
}
Invoke-Checked 'dependency check' { & $Python -c 'import torch, safetensors, numpy, tqdm, gguf, huggingface_hub' }

$ttsDir = Join-Path $Out 'tts'
$source = Join-Path $Out 'src\Qwen3-TTS-12Hz-1.7B-Base'
$scripts = Join-Path $Out 'src\qwen3-tts.cpp'
New-Item -ItemType Directory -Force -Path $ttsDir | Out-Null

if (-not (Test-Path (Join-Path $scripts 'scripts\convert_tts_to_gguf.py'))) {
    # Only the converter scripts of one pinned commit: a depth-1 fetch, no submodules.
    Remove-Item -Recurse -Force $scripts -ErrorAction SilentlyContinue
    Invoke-Checked 'git init' { git init --quiet $scripts }
    Invoke-Checked 'git fetch' { git -C $scripts fetch --quiet --depth 1 $upstream $commit }
    Invoke-Checked 'git checkout' { git -C $scripts checkout --quiet FETCH_HEAD -- scripts }
}

Write-Host "Downloading $ttsRepo ..."
Invoke-Checked 'TTS download' {
    & $Python -c "import sys; from huggingface_hub import snapshot_download; snapshot_download('$ttsRepo', local_dir=sys.argv[1])" $source
}

$talker = Join-Path $ttsDir 'qwen-talker-1.7b-base-F16.gguf'
$tokenizer = Join-Path $ttsDir 'qwen-tokenizer-12hz-F16.gguf'
if (-not (Test-Path $talker)) {
    Invoke-Checked 'talker conversion' { & $Python (Join-Path $scripts 'scripts\convert_tts_to_gguf.py') --input $source --output $talker --type f16 }
}
if (-not (Test-Path $tokenizer)) {
    Invoke-Checked 'tokenizer conversion' { & $Python (Join-Path $scripts 'scripts\convert_tokenizer_to_gguf.py') --input $source --output $tokenizer --type f16 }
}

if (-not $SkipLlm) {
    Write-Host "Downloading $llmRepo/$llmFile ..."
    Invoke-Checked 'LLM download' {
        & $Python -c "import sys; from huggingface_hub import hf_hub_download; hf_hub_download('$llmRepo', '$llmFile', local_dir=sys.argv[1])" (Join-Path $Out 'llm')
    }
}

Write-Host ''
Write-Host "TTS models : $ttsDir   (tts-service/server.py --models)"
if (-not $SkipLlm) { Write-Host "LLM model  : $(Join-Path $Out "llm\$llmFile")   (scripts/start-llm.ps1 -Model)" }
Write-Host "The Hugging Face snapshot in $source can be deleted after conversion."

<#
Start the LLM next to the TTS on an 8 GB GPU.

Gemma 4 E4B QAT q4_0: all layers on the GPU (~2.8 GB with an 8k context). Its per-layer
embedding table stays in RAM and is read lazily (--lazy-mode on); without that, model
loading pushed free RAM below 0.5 GB on a 16 GB machine.

  .\start-llm.ps1 -LlamaServer C:\llama.cpp\llama-server.exe -Model C:\models\gemma-4-E4B-it-q4_0.gguf

Start the TTS first: if the GPU is over-committed, the Windows driver silently moves
allocations to shared system memory instead of failing, and decoding slows down
(measured 66 -> 45 tokens/s). Check per-process "Dedicated GPU memory" in Task Manager.
#>
param(
    [Parameter(Mandatory = $true)][string]$LlamaServer,
    [Parameter(Mandatory = $true)][string]$Model,
    [int]$Port = 18080,
    [int]$Context = 8192
)
& $LlamaServer -m $Model --host 127.0.0.1 --port $Port --offline --no-webui `
    --jinja --reasoning off -ngl 99 -c $Context -np 1 -t 6 -fa on `
    --cache-ram 0 --lazy-mode on --fit off `
    --temp 0.8 --top-k 64 --top-p 0.95 --alias local-llm

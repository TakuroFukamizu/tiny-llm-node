#!/bin/bash
# Download the quantized GGUFs served by the LLM backend.
# All are Q4_K_M — small enough to stay fully VRAM-resident on the 8 GB 4060,
# which keeps runtime PCIe traffic (and the issue-#2 brownout risk) low.
set -e

DST=/home/pi/comfy/models/llm
mkdir -p "$DST"
cd "$DST"

dl(){ echo "### $(date +%T) $2"; wget -c -q --show-progress -O "$2" "$1" 2>&1 | tail -1; }

HF=https://huggingface.co

# Primary dialogue (8080) — good at JSON/structured output and function-calling.
dl "$HF/bartowski/Qwen2.5-3B-Instruct-GGUF/resolve/main/Qwen2.5-3B-Instruct-Q4_K_M.gguf"   Qwen2.5-3B-Instruct-Q4_K_M.gguf
# Fast secondary (8081) — intent/slot classification, short replies.
dl "$HF/bartowski/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf" Qwen2.5-1.5B-Instruct-Q4_K_M.gguf
# Extra tiny (fastest) and an alternate voice — optional.
dl "$HF/bartowski/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/Qwen2.5-0.5B-Instruct-Q4_K_M.gguf" Qwen2.5-0.5B-Instruct-Q4_K_M.gguf
dl "$HF/bartowski/Llama-3.2-3B-Instruct-GGUF/resolve/main/Llama-3.2-3B-Instruct-Q4_K_M.gguf"  Llama-3.2-3B-Instruct-Q4_K_M.gguf

echo
echo "Models in $DST:"
ls -lh "$DST"/*.gguf

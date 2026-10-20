#!/bin/bash
# usage: t2i.sh "prompt" [W] [H] [steps] [out.png]
source /home/pi/comfy/qwen-image.env
mountpoint -q /home/pi/comfy || /home/pi/comfy-mount.sh
sudo nvidia-smi -pm 1 >/dev/null 2>&1; sudo nvidia-smi -pl 90 >/dev/null 2>&1
P="${1:?prompt required}"; W="${2:-768}"; H="${3:-768}"; S="${4:-20}"; O="${5:-$QIM_OUT/t2i_$(date +%s).png}"
"$SD_BIN" --diffusion-model "$QIM_DIFF" --llm "$QIM_LLM" --vae "$QIM_VAE" \
  -p "$P" -W "$W" -H "$H" --steps "$S" --cfg-scale 2.5 --sampling-method euler -o "$O"
echo "saved: $O"

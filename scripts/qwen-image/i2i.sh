#!/bin/bash
# usage: i2i.sh init.png "prompt" [strength] [steps] [out.png]
source /home/pi/comfy/qwen-image.env
mountpoint -q /home/pi/comfy || /home/pi/comfy-mount.sh
sudo nvidia-smi -pm 1 >/dev/null 2>&1; sudo nvidia-smi -pl 90 >/dev/null 2>&1
I="${1:?init image required}"; P="${2:?prompt required}"; STR="${3:-0.6}"; S="${4:-20}"; O="${5:-$QIM_OUT/i2i_$(date +%s).png}"
"$SD_BIN" --diffusion-model "$QIM_DIFF" --llm "$QIM_LLM" --vae "$QIM_VAE" \
  -i "$I" --strength "$STR" -p "$P" --steps "$S" --cfg-scale 2.5 --sampling-method euler -o "$O"
echo "saved: $O"

#!/bin/bash
# usage: edit.sh ref.png "edit instruction" [steps] [out.png]  (複数参照は環境変数 QIM_REF2 で追加可)
source /home/pi/comfy/qwen-image.env
mountpoint -q /home/pi/comfy || /home/pi/comfy-mount.sh
sudo nvidia-smi -pm 1 >/dev/null 2>&1; sudo nvidia-smi -pl 90 >/dev/null 2>&1
R="${1:?ref image required}"; P="${2:?instruction required}"; S="${3:-20}"; O="${4:-$QIM_OUT/edit_$(date +%s).png}"
EXTRA=(); [ -n "$QIM_REF2" ] && EXTRA=(-r "$QIM_REF2")
"$SD_BIN" --diffusion-model "$QIM_DIFF" --llm "$QIM_LLM" --llm_vision "$QIM_MMPROJ" --vae "$QIM_VAE" \
  -r "$R" "${EXTRA[@]}" -p "$P" --steps "$S" --cfg-scale 2.5 --sampling-method euler --vae-tiling -o "$O"
echo "saved: $O"

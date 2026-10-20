#!/bin/bash
# Start ComfyUI for the Qwen-Image-2.1 int8 path, reachable from another PC on the LAN.
#
# This path runs on CUDA (comfy_kitchen), which carries the issue-#2 brownout risk
# that the Vulkan sd.cpp path avoids. It also needs the full 8 GB of VRAM, so the
# llama.cpp LLM services (8080/8081) are stopped first to free it.
#
# Usage: ./comfyui-serve.sh        # then open http://<node-ip>:8188 from another PC
set -e

COMFY=/home/pi/comfy/ComfyUI

# 1. Free VRAM — stop the LLM API services if they are running.
sudo systemctl stop llama-primary llama-intent 2>/dev/null || true

# 2. Apply the 90 W power cap (brownout mitigation for #2).
sudo nvidia-smi -pm 1 >/dev/null 2>&1 || true
sudo nvidia-smi -pl 90 >/dev/null 2>&1 || true

# 3. Launch ComfyUI bound to all interfaces so another PC can open the UI.
cd "$COMFY"
setsid nohup ./venv/bin/python main.py --listen 0.0.0.0 --port 8188 \
  > /home/pi/comfy/comfyui.log 2>&1 &

echo "ComfyUI starting on http://0.0.0.0:8188 (log: /home/pi/comfy/comfyui.log)"
echo "Open http://<this-node-ip>:8188 from the other PC, then load a workflow from"
echo "the Workflows sidebar (comfyui-workflows/*.json installed under user/default/workflows/)."
echo
echo "When done, restart the LLM API:  sudo systemctl start llama-primary llama-intent"

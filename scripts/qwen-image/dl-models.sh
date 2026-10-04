#!/bin/bash
cd /home/pi/comfy/ComfyUI
B=https://huggingface.co/unsloth
dl(){ echo "### $(date +%T) DL $2"; wget -c -q --show-progress -O "$2" "$1" 2>&1 | tail -1; }
dl "$B/Qwen-Image-2.1-GGUF/resolve/main/qwen-image-2.1-Q4_K_M.gguf"        models/unet/qwen-image-2.1-Q4_K_M.gguf
dl "$B/Qwen-Image-2.1-FP8/resolve/main/vae/qwen_image_2.1_vae_bf16.safetensors" models/vae/qwen_image_2.1_vae_bf16.safetensors
dl "$B/Qwen3-VL-8B-Instruct-GGUF/resolve/main/Qwen3-VL-8B-Instruct-UD-Q4_K_XL.gguf" models/text_encoders/Qwen3-VL-8B-Instruct-UD-Q4_K_XL.gguf
dl "$B/Qwen3-VL-8B-Instruct-GGUF/resolve/main/mmproj-F16.gguf"             models/text_encoders/Qwen3-VL-8B-Instruct-mmproj-F16.gguf
echo "### $(date +%T) ALL DONE"
ls -lh models/unet/ models/vae/ models/text_encoders/

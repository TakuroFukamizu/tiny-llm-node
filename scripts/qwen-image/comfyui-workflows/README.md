# ComfyUI workflows — Qwen-Image-2.1 int8

Ready-to-load ComfyUI graphs for the **int8 path** (`comfy_kitchen` / CUDA).
See [Appendix A of the image-generation guide](../../../docs/image_generation_qwen_image.md#appendix-a--the-comfyui-int8-path-validated)
for the full setup; this path is validated but **sd.cpp + Vulkan is still preferred
for production** (lower memory, no issue-#2 brownout risk).

| File | Mode | Notes |
|---|---|---|
| `qwen-image-2.1-int8-t2i.json` | Text → Image | `KSampler` denoise 1.0, 8 steps |
| `qwen-image-2.1-int8-i2i.json` | Image → Image | `LoadImage` → `VAEEncode` → denoise 0.6, 10 steps |
| `qwen-image-2.1-int8-edit.json` | Instruction edit | reference image into `TextEncodeQwenImage21.images`, denoise 1.0 |

All three use the native int8 weights with a standard `CLIPLoader` (not
`CLIPLoaderGGUF`) to avoid the 12288-dim text-encoder mismatch:

- `UNETLoader` → `qwen_image_2.1_int8_convrot.safetensors`
- `CLIPLoader` → `qwen3vl_8b_int8_convrot.safetensors`, type `qwen_image`
- `VAELoader` → `qwen_image_2.1_vae_comfyorg.safetensors`
- `ModelSamplingAuraFlow` shift `3.1`, `KSampler` euler / simple / cfg `2.5`

## Install (make them appear in the UI sidebar)

```bash
cp scripts/qwen-image/comfyui-workflows/*.json \
   /home/pi/comfy/ComfyUI/user/default/workflows/
```

Then start ComfyUI with [`../comfyui-serve.sh`](../comfyui-serve.sh) and open
`http://<node-ip>:8188` from another PC. The workflows show up under the
**Workflows** sidebar (folder icon); open one, edit the prompt, and Run.
Keep to **512 px / 8–10 steps** first — larger/longer raises the #2 risk.

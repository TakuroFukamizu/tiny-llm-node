# Image Generation — Qwen-Image-2.1 (4-bit) on Pi 5 + RTX 4060

**Language:** English | [日本語](image_generation_qwen_image_ja.md)

Reproducible procedure for text-to-image (T2I), image-to-image (I2I), and
instruction-based image **Edit** on this node, using the unsloth
**Qwen-Image-2.1 4-bit GGUF** diffusion model.

Verified working on 2026-09-25 (Pi 5 8GB + RTX 4060 8GB, PCIe Gen 2 x1 eGPU).

---

## TL;DR — the key finding

**The unsloth Qwen-Image-2.1 4-bit GGUF runs via
[`stable-diffusion.cpp`](https://github.com/leejet/stable-diffusion.cpp) with
the Vulkan backend — NOT via ComfyUI.** All three modes (T2I / I2I / Edit)
generate images and run GPU diffusion to completion without the power-brownout
crash that kills CUDA workloads on this node (hardware issue #2, below).

Why not ComfyUI:

1. **The unsloth 4-bit GGUF has no `general.architecture` metadata** and is
   built for sd.cpp, so ComfyUI (and ComfyUI-GGUF) cannot identify the model
   architecture and refuses to load it. ComfyUI-GGUF does not support the
   Qwen-Image-2.1 DiT.
2. The **ComfyUI-native path requires either bf16 or int8**:
   - bf16 weights do not fit in 8 GB VRAM.
   - The int8 (`int8_convrot`) path depends on the `comfy_kitchen` backend,
     which requires **PyTorch built for CUDA 13.0 (cu130)**. A cu130 aarch64
     wheel (`torch 2.11.0+cu130`) was eventually located and the kitchen
     backend did report `available: True, disabled: False`, but the final
     int8 generation test on ComfyUI was never completed and is not needed —
     the sd.cpp path already satisfies every requirement.
3. **sd.cpp recognizes Qwen-Image-2.1 and Qwen3-VL natively** and runs the
   4-bit GGUF directly. This is the supported, proven path and the one this
   document describes.

> If you are resuming the abandoned ComfyUI int8 experiment, see
> [Appendix A](#appendix-a--the-comfyui-int8-path-not-recommended). For normal
> use, ignore ComfyUI entirely.

---

## 1. Prerequisites

This builds on the NVIDIA driver + kernel setup already documented. Do **not**
repeat it here — follow [software_setup.md](software_setup.md) first and confirm:

- NVIDIA driver **580.95.05** (open kernel modules, ARM-patched), 4K-page
  kernel `kernel8.img`, config **frozen**. `nvidia-smi` shows the RTX 4060.
- **Vulkan works through the NVIDIA driver** — no CUDA Toolkit (`nvcc`) is
  installed, and none is needed. Verify:
  ```bash
  vulkaninfo --summary | grep -i "deviceName"
  # => GPU0: NVIDIA GeForce RTX 4060   (GPU1 is the Pi's V3D — ignore it)
  ```
- Build tools present: `cmake`, `glslc`, `libvulkan-dev`, `ccache`, a C++
  compiler. (These are installed on this node.)

### Hardware issue #2 — power brownout (read before running anything)

The Pi and the GPU share a **single USB-PD → DC-DC rail** (via the X1010
carrier). On a large VRAM transfer or sustained CUDA compute the GPU current
spike sags the shared rail and **hard-resets the whole Pi** (confirmed via
persistent journald: the boot log cuts off mid-`load_tensors`, no panic, ext4
orphan cleanup on next boot). A GPU power cap alone did not stop it —
`nvidia-smi -pl 90` still crashed under sustained ollama/CUDA load.

**Why Vulkan is the stable path:** empirically, `stable-diffusion.cpp` with
the **Vulkan** backend ran sustained GPU diffusion to completion **without**
crashing, where the CUDA path did not. We do not have a definitive electrical
explanation, but Vulkan + sequential component loading + the 90 W cap is the
only combination proven to finish image jobs on this node. **Always apply the
cap before generating** (the wrapper scripts do this automatically):

```bash
sudo nvidia-smi -pm 1       # persistence mode
sudo nvidia-smi -pl 90      # 90 W cap (RTX 4060 valid range is 90–115 W; 60 W is rejected)
```

Even with the cap, large resolutions / long runs retain residual crash risk.
**Start small (512 px, 10 steps).**

---

## 2. Storage layout — ext4-image-on-SSD

**Problem:** the SD-card root is small (~29 GB, tight) and cannot hold the
diffusion stack (models alone are ~15 GB; plus the sd.cpp build and outputs).
The 1.9 TB SSD is formatted **exFAT** (`/media/pi/SUNEAST`), which cannot store
a Linux build tree with correct permissions/symlinks or memory-map large
model files well.

**Solution:** create a single large **ext4 filesystem inside an image file** on
the exFAT SSD, and loop-mount it at `/home/pi/comfy`. Everything — the sd.cpp
build, the Python venv, the models, and outputs — lives inside that ext4 image.

### Create the image (one-time)

```bash
# A sparse 64 GB ext4 image on the SSD (adjust size to taste; we used ~61 GB usable)
truncate -s 64G /media/pi/SUNEAST/comfyui-ext4.img
mkfs.ext4 -m 0 /media/pi/SUNEAST/comfyui-ext4.img   # -m 0: no reserved-root blocks
mkdir -p /home/pi/comfy
sudo mount -o loop /media/pi/SUNEAST/comfyui-ext4.img /home/pi/comfy
sudo chown pi:pi /home/pi/comfy
```

> Note: exFAT does not support `fallocate`/sparse files, so on this node the
> image was grown with `dd` appends rather than `truncate` when enlarging. For a
> first creation either works; `truncate` is simplest.

### Grow the image later (if you run low)

```bash
sudo umount /home/pi/comfy
truncate -s +32G /media/pi/SUNEAST/comfyui-ext4.img   # add 32 GB
e2fsck -f /media/pi/SUNEAST/comfyui-ext4.img
resize2fs /media/pi/SUNEAST/comfyui-ext4.img          # grow fs to fill the image
sudo mount -o loop /media/pi/SUNEAST/comfyui-ext4.img /home/pi/comfy
```

(Online growth is also possible without unmounting: append to the backing file,
`sudo losetup -c /dev/loopN` to refresh capacity, then `sudo resize2fs /dev/loopN`.)

### Remount after every reboot — `comfy-mount.sh`

The image is **not** in `/etc/fstab`; it must be mounted after each boot (and
after the SSD is plugged in). The wrapper scripts call this automatically, but
you can run it by hand (script committed at [`scripts/qwen-image/comfy-mount.sh`](../scripts/qwen-image/comfy-mount.sh)):

```bash
# /home/pi/comfy-mount.sh
#!/bin/bash
IMG=/media/pi/SUNEAST/comfyui-ext4.img; MNT=/home/pi/comfy
mountpoint -q "$MNT" && { echo "already mounted"; exit 0; }
[ -f "$IMG" ] || { echo "image not found (SSD mounted at /media/pi/SUNEAST?)"; exit 1; }
sudo mount -o loop "$IMG" "$MNT" && sudo chown pi:pi "$MNT" && echo "mounted $MNT"
```

```bash
mountpoint -q /home/pi/comfy || /home/pi/comfy-mount.sh
```

> Prefer `/home/pi/comfy` for **all** large downloads and builds. Do not put
> multi-GB model files on the SD-card root.

---

## 3. Build stable-diffusion.cpp with Vulkan

```bash
mountpoint -q /home/pi/comfy || /home/pi/comfy-mount.sh
cd /home/pi/comfy
git clone --recursive https://github.com/leejet/stable-diffusion.cpp
cd stable-diffusion.cpp

# Vulkan backend (NOT CUDA — see issue #2). No nvcc required.
cmake -B build -DSD_VULKAN=ON -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j$(nproc)
```

Build artifacts:

- `build/bin/sd-cli`    — the CLI used below
- `build/bin/sd-server` — optional HTTP server (not used in this guide)

**Verify the Vulkan backend picks the RTX 4060.** The first lines of any run
should show:

```
ggml_vulkan: Found 2 Vulkan devices:
ggml_vulkan: 0 = NVIDIA GeForce RTX 4060 (NVIDIA) | ... matrix cores: NV_coopmat2
ggml_vulkan: 1 = V3D 7.1.10.2 (V3DV Mesa) | ...        <- Pi's iGPU, device 0 is used
```

> **Why Vulkan over CUDA here:** (1) no CUDA Toolkit is installed and the frozen
> driver config must not be disturbed to add one; (2) the CUDA path triggers
> the issue-#2 brownout under sustained compute, while the Vulkan path has run
> diffusion to completion repeatedly without crashing. Vulkan reaches the RTX
> 4060 through the NVIDIA driver's Vulkan ICD, so no toolkit is involved.

---

## 4. Model files

All four files live under `ComfyUI/models/` inside the ext4 image (the directory
layout predates the decision to use sd.cpp; sd.cpp just reads them by absolute
path). Download with [`scripts/qwen-image/dl-models.sh`](../scripts/qwen-image/dl-models.sh)
(uses `wget -c`, resumable):

| Role | File | Size | Source (HF, `unsloth/…`) |
|---|---|---|---|
| Diffusion (DiT), 4-bit | `models/unet/qwen-image-2.1-Q4_K_M.gguf` | 4.0 GB | `Qwen-Image-2.1-GGUF/resolve/main/qwen-image-2.1-Q4_K_M.gguf` |
| Text encoder (LLM), 4-bit | `models/text_encoders/Qwen3-VL-8B-Instruct-UD-Q4_K_XL.gguf` | 4.8 GB | `Qwen3-VL-8B-Instruct-GGUF/resolve/main/Qwen3-VL-8B-Instruct-UD-Q4_K_XL.gguf` |
| Vision projector (Edit only) | `models/text_encoders/Qwen3-VL-8B-Instruct-mmproj-F16.gguf` | 1.1 GB | `Qwen3-VL-8B-Instruct-GGUF/resolve/main/mmproj-F16.gguf` |
| VAE | `models/vae/qwen_image_2.1_vae_bf16.safetensors` | 645 MB | `Qwen-Image-2.1-FP8/resolve/main/vae/qwen_image_2.1_vae_bf16.safetensors` |

```bash
cd /home/pi/comfy/ComfyUI
B=https://huggingface.co/unsloth
dl(){ echo "### $(date +%T) DL $2"; wget -c -q --show-progress -O "$2" "$1" 2>&1 | tail -1; }
dl "$B/Qwen-Image-2.1-GGUF/resolve/main/qwen-image-2.1-Q4_K_M.gguf"              models/unet/qwen-image-2.1-Q4_K_M.gguf
dl "$B/Qwen-Image-2.1-FP8/resolve/main/vae/qwen_image_2.1_vae_bf16.safetensors" models/vae/qwen_image_2.1_vae_bf16.safetensors
dl "$B/Qwen3-VL-8B-Instruct-GGUF/resolve/main/Qwen3-VL-8B-Instruct-UD-Q4_K_XL.gguf" models/text_encoders/Qwen3-VL-8B-Instruct-UD-Q4_K_XL.gguf
dl "$B/Qwen3-VL-8B-Instruct-GGUF/resolve/main/mmproj-F16.gguf"                   models/text_encoders/Qwen3-VL-8B-Instruct-mmproj-F16.gguf
```

> **Use the unsloth VAE** (`qwen_image_2.1_vae_bf16.safetensors`). sd.cpp loads
> it correctly. A Comfy-Org VAE (`qwen_image_2.1_vae_comfyorg.safetensors`,
> also 645 MB) exists from the abandoned ComfyUI experiment — it is **only** for
> ComfyUI; do not pass it to sd.cpp (see Troubleshooting).

---

## 5. Running — sd-cli commands

### Environment file — [`scripts/qwen-image/qwen-image.env`](../scripts/qwen-image/qwen-image.env)

```bash
# /home/pi/comfy/qwen-image.env
export SD_BIN="/home/pi/comfy/stable-diffusion.cpp/build/bin/sd-cli"
export QIM_DIFF="/home/pi/comfy/ComfyUI/models/unet/qwen-image-2.1-Q4_K_M.gguf"
export QIM_LLM="/home/pi/comfy/ComfyUI/models/text_encoders/Qwen3-VL-8B-Instruct-UD-Q4_K_XL.gguf"
export QIM_MMPROJ="/home/pi/comfy/ComfyUI/models/text_encoders/Qwen3-VL-8B-Instruct-mmproj-F16.gguf"
export QIM_VAE="/home/pi/comfy/ComfyUI/models/vae/qwen_image_2.1_vae_bf16.safetensors"
export QIM_OUT="/home/pi/comfy/outputs"
```

### Flag reference

| Flag | Meaning |
|---|---|
| `--diffusion-model` | Qwen-Image-2.1 DiT GGUF (the 4-bit diffusion model) |
| `--llm` | Qwen3-VL text-encoder GGUF (turns the prompt into conditioning) |
| `--llm_vision` | mmproj GGUF — the vision projector; **required for Edit** so the model can "see" the reference image |
| `--vae` | VAE safetensors (encode/decode between pixel and latent space) |
| `-p` | prompt (T2I/I2I: description; Edit: the edit instruction) |
| `-W` / `-H` | output width / height in pixels |
| `--steps` | sampler steps — 10 = fast, 20 = higher quality |
| `--cfg-scale 2.5` | classifier-free guidance; **2.5 is the Qwen-Image default** |
| `--sampling-method euler` | sampler; **euler is the Qwen-Image default** |
| `-i` / `--strength` | **I2I:** init image / denoise strength (0 = keep init, 1 = follow prompt; ~0.6 typical) |
| `-r` | **Edit:** reference image to edit (repeat `-r` for multiple references) |
| `--flow-shift` | flow-matching timestep shift (a.k.a. `shift`); leave default unless tuning — raising it biases toward structure |
| `--vae-tiling` | tile the VAE decode to cut peak VRAM; used in Edit mode to avoid VAE-decode warnings/OOM |
| `-o` | output PNG path |

### T2I — text to image

```bash
source /home/pi/comfy/qwen-image.env
sudo nvidia-smi -pm 1; sudo nvidia-smi -pl 90
"$SD_BIN" --diffusion-model "$QIM_DIFF" --llm "$QIM_LLM" --vae "$QIM_VAE" \
  -p "a cute red panda on a bamboo branch" \
  -W 512 -H 512 --steps 10 --cfg-scale 2.5 --sampling-method euler \
  -o "$QIM_OUT/t2i.png"
```

### I2I — image to image

Add an init image and a strength:

```bash
"$SD_BIN" --diffusion-model "$QIM_DIFF" --llm "$QIM_LLM" --vae "$QIM_VAE" \
  -i init.png --strength 0.6 \
  -p "watercolor painting style" \
  --steps 10 --cfg-scale 2.5 --sampling-method euler \
  -o "$QIM_OUT/i2i.png"
```

### Edit — instruction-based editing

Add the vision projector (`--llm_vision`) and a reference image (`-r`); the
prompt becomes the edit instruction. `--vae-tiling` relieves VRAM pressure:

```bash
"$SD_BIN" --diffusion-model "$QIM_DIFF" --llm "$QIM_LLM" --llm_vision "$QIM_MMPROJ" --vae "$QIM_VAE" \
  -r photo.png \
  -p "Change the background to a snowy forest" \
  --steps 10 --cfg-scale 2.5 --sampling-method euler --vae-tiling \
  -o "$QIM_OUT/edit.png"
# multiple references: add more -r flags, e.g. -r a.png -r b.png -p "combine ..."
```

### Convenience wrappers

Three wrappers in `/home/pi/comfy/` (committed under
[`scripts/qwen-image/`](../scripts/qwen-image/)) source the env file, ensure the
mount, apply the 90 W cap, and default resolution/steps. Output goes to
`/home/pi/comfy/outputs/`.

```bash
# text-to-image:  prompt [W] [H] [steps] [out.png]
/home/pi/comfy/t2i.sh "a cute red panda on a bamboo branch" 768 768 20

# image-to-image: init.png prompt [strength] [steps] [out.png]
/home/pi/comfy/i2i.sh input.png "watercolor painting style" 0.6 20

# image edit:     ref.png "instruction" [steps] [out.png]   (2nd ref via QIM_REF2)
/home/pi/comfy/edit.sh photo.png "Change the background to a snowy forest" 20
QIM_REF2=other.png /home/pi/comfy/edit.sh a.png "combine the two scenes" 20
```

The wrappers default to **768×768 / 20 steps**; drop to **512×512 / 10 steps**
for the fastest, lowest-risk runs.

---

## 6. Measured performance & stability

Measured 2026-09-25, 512×512 / 10 steps, cfg 2.5, euler, Vulkan, 90 W cap.
Timings are **end-to-end including model load** (cold); the GPU is loaded
sequentially (diffusion → text encoder → VAE), so each stage streams across the
PCIe Gen 2 x1 link once.

| Mode | End-to-end | Text-encode (cond.) | Sampling | VAE | Notes |
|---|---|---|---|---|---|
| T2I | **~159 s** | ~36 s | ~106 s | ~17 s decode | 512²/10 steps |
| I2I | **~61 s** | — | — | — | init + strength 0.6, faster (fewer effective steps) |
| Edit | longer than T2I | ~71 s | — | tiled | mmproj conditioning dominates; most VRAM-hungry |

All three modes **completed without crashing** on the Vulkan path.

Stability caveats (issue #2):

- **Always** `sudo nvidia-smi -pm 1 && sudo nvidia-smi -pl 90` first (wrappers
  do this).
- **Start small: 512 px, 10 steps.** Scale up to 768 px / 20 steps only once a
  run succeeds. 1024 px enables Qwen-Image's native-2K quality but raises VRAM,
  runtime, **and** brownout risk.
- Edit mode uses the most VRAM (mmproj + reference encode); keep it at 512 px
  and use `--vae-tiling`.
- Quality vs. risk: `--steps 20+` improves quality at proportional time/risk
  cost.

---

## 7. Troubleshooting

- **ComfyUI "cannot detect model architecture" / won't load the GGUF.**
  Expected. The unsloth 4-bit GGUF lacks `general.architecture` metadata and is
  built for sd.cpp. Use sd.cpp — do not try to load it in ComfyUI.

- **VAE architecture mismatch in sd.cpp.** Pass the **unsloth** VAE
  (`qwen_image_2.1_vae_bf16.safetensors`). The `*_comfyorg.safetensors` VAE is
  for ComfyUI only; feeding it to sd.cpp (or vice-versa) causes an arch/shape
  mismatch at VAE load.

- **Qwen3-VL "12288-dim" / tensor-shape error (ComfyUI only).** This came from
  loading the Qwen3-VL text encoder as GGUF in ComfyUI. Irrelevant to the sd.cpp
  path. (In ComfyUI it was worked around by loading the native int8 safetensors
  via the standard `CLIPLoader` — see Appendix A.)

- **VRAM pressure / OOM in Edit mode.** mmproj + reference-image encode add
  pressure. Use `--vae-tiling`, stay at 512 px, reduce steps, and avoid multiple
  `-r` references unless needed.

- **Pi reboots mid-generation (brownout, issue #2).** Confirm the 90 W cap is
  applied (`nvidia-smi -q -d POWER | grep 'Power Limit'`). Reduce resolution and
  steps. If it persists, the real fix is electrical — separate GPU power from
  the Pi's rail (see [power.md](power.md)). Vulkan is the mitigation, not a
  guarantee.

- **"image not found" from a wrapper.** The ext4 image isn't mounted and/or the
  SSD isn't at `/media/pi/SUNEAST`. Run `/home/pi/comfy-mount.sh`.

- **Only the Pi's V3D GPU shows in Vulkan, not the 4060.** The NVIDIA driver /
  Vulkan ICD isn't loaded — re-check `nvidia-smi` and the driver per
  [software_setup.md](software_setup.md).

---

## Appendix A — the ComfyUI int8 path (not recommended)

Recorded for completeness; **not required** and never fully validated. The goal
was to run Qwen-Image-2.1 int8 inside ComfyUI proper. Status when abandoned:

- `torch 2.11.0+cu130` (aarch64/cp313) **was** found and installed
  (`PIP_CONFIG_FILE=/dev/null` required to stop piwheels interfering), enabling
  the `comfy_kitchen` CUDA backend (`available: True, disabled: False`), which
  provides the `int8_convrot` GEMM ops.
- Native int8 weights were downloaded:
  `diffusion_models/qwen_image_2.1_int8_convrot.safetensors` (6.8 GB) and
  `text_encoders/qwen3vl_8b_int8_convrot.safetensors` (8.8 GB), plus the
  Comfy-Org VAE.
- The text encoder had to be loaded as **native safetensors via the standard
  `CLIPLoader`** (type `qwen_image`), **not** `CLIPLoaderGGUF`, to avoid the
  12288-dim mismatch.
- The final int8 generation test was never run. Even if it works, it runs on
  the **CUDA** path, which carries the issue-#2 brownout risk that the Vulkan
  sd.cpp path avoids. **Prefer sd.cpp.**

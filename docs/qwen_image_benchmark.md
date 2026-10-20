# Benchmark — sd.cpp vs ComfyUI for Qwen-Image-2.1 on Pi 5 + RTX 4060

**Language:** English | [日本語](qwen_image_benchmark_ja.md)

Head-to-head generation-time comparison of the two image paths on this node:

- **stable-diffusion.cpp** (Vulkan) with the **unsloth 4-bit GGUF** — the
  recommended path ([image_generation_qwen_image.md](image_generation_qwen_image.md)).
- **ComfyUI int8** (`comfy_kitchen`, CUDA) with the **int8 safetensors** —
  the node-UI path ([Appendix A](image_generation_qwen_image.md#appendix-a--the-comfyui-int8-path-validated)).

> This is a **time** comparison. The two paths use different quantizations
> (4-bit GGUF vs int8), so output is not pixel-identical; quality is comparable
> at these settings but not the subject here.

## Result in one line

- **Iterating on one prompt (vary the seed):** ComfyUI **warm** wins hard —
  **~8–18 s** vs sd.cpp's **~150–170 s**, because ComfyUI caches the text
  conditioning and only re-runs the sampler.
- **A different prompt each image (typical) or a single one-off:** **sd.cpp
  wins** — **~148–170 s** vs ComfyUI **~232–240 s**. ComfyUI's int8 weights
  (6.8 + 8.8 GB) overflow the 8 GB VRAM, so every new prompt reloads/swaps the
  8.8 GB text encoder; sd.cpp's lighter 4-bit load is cheaper per image.
- **Edit:** sd.cpp works (**~166 s**); ComfyUI int8 Edit **crashed**
  (cuDNN / KSampler), consistent with the known CUDA instability.

## Method

- Node: Pi 5 (8 GB) + RTX 4060 (8 GB), 90 W power cap, no CUDA toolkit.
- Settings for every run: **512×512, cfg 2.5, euler / simple**. T2I at 8 and 20
  steps; I2I at 10 steps, denoise 0.6; Edit at 10 steps. I2I/Edit use the same
  512×512 seed image.
- sd.cpp via `sd-cli` (one process per image — reloads the model every call).
- ComfyUI via its HTTP API against a resident server (model stays loaded between
  calls). Wall time measured from submit to completion.
- `boot_id` watched throughout — **no #2 brownout occurred** on either path at
  these settings.
- Small sample (n=2 for most cells); treat as order-of-magnitude, not ±1 s.

### A caching caveat that shaped the method

ComfyUI returns a **cached** result (~2 s) when the graph *and* seed are
identical, which is not a real generation. So ComfyUI "warm" was measured two
ways: **(a) same prompt, new seed each run** (the text-conditioning stays
cached, only the sampler re-runs) and **(b) a brand-new prompt each run** (the
text encoder must re-run). These are very different, and both are reported.

## Numbers (512×512)

### ComfyUI int8 (CUDA, resident server)

| Mode | Steps | Cold (1st run, model load) | Warm — same prompt, new seed | Warm — new prompt |
|---|---|---|---|---|
| T2I | 20 | 232 s | **14–18 s** | ~232 s |
| T2I | 8 | — | **8 s** | **236 s** (×2) |
| I2I | 10 | — | — | **240 s** |
| Edit | 10 | — | — | **FAILED** (`exec_error:KSampler`, cuDNN) |

### sd.cpp 4-bit GGUF (Vulkan, `sd-cli` — reloads every call)

| Mode | Steps | Run 1 | Run 2 |
|---|---|---|---|
| T2I | 20 | 168.6 s | 172.8 s |
| T2I | 8 | 148.2 s | 147.1 s |
| I2I | 10 | 146.6 s | — |
| Edit | 10 | 166.4 s | — |

### Where sd.cpp's time goes (per `sd-cli` call, 8-step T2I ≈ 148 s)

| Phase | Time | Note |
|---|---|---|
| Text encoder load + encode | ~70 s | Qwen3-VL runs on **CPU** (params on CPU/RAM) |
| Diffusion model load | ~52 s | 4-bit DiT into VRAM |
| VAE load | ~6 s | |
| Sampling (8 steps) | ~15 s | ~1.9 s/step |
| Decode | ~1 s | |
| **Model loading total** | **~113 s (~76 %)** | paid **every** CLI call |

So sd.cpp is **load-bound**: ~¾ of each call is loading ~9 GB of weights. The
actual compute is only ~30 s. A resident server (ComfyUI, or sd.cpp's
`sd-server`) is what amortizes this away.

## Interpretation

- **ComfyUI's win is narrow but huge where it applies.** If you keep one prompt
  and sweep seeds/steps, the cached conditioning makes each extra image cost
  only the sampler (~8–18 s). Nothing else here is close.
- **ComfyUI loses on new prompts because of VRAM.** The int8 text encoder alone
  is 8.8 GB — bigger than the whole card — so each new prompt pays a full
  reload/swap (~230 s), landing *slower* than sd.cpp's complete 4-bit run.
- **sd.cpp is steady and robust.** Every call is ~150–170 s regardless of prompt,
  Edit included, on the crash-proof Vulkan path. No cache cliffs, no cuDNN
  failures, no #2 risk at these settings.

## Recommendation

| You are doing… | Use |
|---|---|
| Interactive seed/step exploration of **one** prompt | **ComfyUI warm** (node UI) |
| A batch of **different** prompts, or unattended runs | **sd.cpp** (Vulkan) |
| **Image Edit**, or anything that must not crash | **sd.cpp** (Vulkan) |
| Larger resolutions / long runs (#2 risk) | **sd.cpp** (Vulkan) |

Default to **sd.cpp + Vulkan**; reach for the ComfyUI warm path specifically
when iterating on a single prompt. To make sd.cpp fast for repeated calls, run
its `sd-server` so the model stays resident instead of reloading per image.

---

*Raw results: 13-job matrix plus warm/new-prompt re-measurements, run 2026-10-20.
No #2 brownout; `boot_id` constant throughout.*

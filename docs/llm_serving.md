# LLM Serving — local OpenAI-compatible API on Pi 5 + RTX 4060

**Language:** English | [日本語](llm_serving_ja.md)

Serve several small quantized LLMs over the LAN as an OpenAI-compatible API,
using **llama.cpp with the Vulkan backend**. Built as a game AI backend (NPC
dialogue + fast intent classification for a Unity client); the
Unity-facing API reference is in [game_ai_backend.md](game_ai_backend.md).

## TL;DR

- **llama.cpp + Vulkan**, not CUDA/ollama. Vulkan is the only GPU path proven
  stable on this node — sustained CUDA compute brownouts the shared power rail
  and hard-resets the Pi (hardware issue #2, see [power.md](power.md)).
- Two always-on services, both OpenAI-compatible:
  - **:8080** Qwen2.5-3B-Instruct — dialogue, JSON/structured output, tools (~75 tok/s)
  - **:8081** Qwen2.5-1.5B-Instruct — fast intent/slot classification (~140 tok/s)
- Runs as **systemd services**: auto-start on boot, auto-restart on crash
  (including a #2 reset), 90 W GPU cap applied first.
- Small Q4_K_M models stay fully VRAM-resident → no runtime PCIe traffic → stays
  inside the safe power envelope.

## Prerequisites

- NVIDIA driver 580.95.05 (open modules) up, Vulkan ICD present. See
  [software_setup.md](software_setup.md). `nvidia-smi` must work and
  `vulkaninfo --summary` must list the RTX 4060 (not just the Pi's V3D).
- ollama stopped and disabled (it uses CUDA and hard-crashes under load):
  `sudo systemctl disable --now ollama 2>/dev/null || true`.

## 1. Build llama.cpp (Vulkan)

[`services/llm/build-llama-cpp.sh`](../services/llm/build-llama-cpp.sh):

```bash
cmake -B build -DGGML_VULKAN=ON -DCMAKE_BUILD_TYPE=Release
cmake --build build -j"$(nproc)" --target llama-server llama-cli
```

No CUDA toolkit / nvcc — the driver supplies the Vulkan ICD. Binary lands at
`/home/pi/llama.cpp/build/bin/llama-server`.

## 2. Download models

[`services/llm/download-models.sh`](../services/llm/download-models.sh) fetches
the GGUFs into `/home/pi/comfy/models/llm/` (resumable `wget -c`):

| File | Size | Role |
|---|---|---|
| `Qwen2.5-3B-Instruct-Q4_K_M.gguf` | 1.8 GB | primary dialogue (8080) |
| `Qwen2.5-1.5B-Instruct-Q4_K_M.gguf` | 941 MB | fast classification (8081) |
| `Qwen2.5-0.5B-Instruct-Q4_K_M.gguf` | 380 MB | tiny/fastest (optional) |
| `Llama-3.2-3B-Instruct-Q4_K_M.gguf` | 1.9 GB | alternate voice (optional) |

A 7B is intentionally **not** downloaded — the larger the model, the larger the
VRAM transfer and the higher the #2 risk.

## 3. Install the services

[`services/llm/install.sh`](../services/llm/install.sh) copies the three unit
files, reloads systemd, verifies there is no ordering cycle, and enables them:

```bash
cd services/llm
./install.sh
```

The units:

| Unit | Purpose |
|---|---|
| `gpu-powercap.service` | oneshot: `nvidia-smi -pm 1` + `-pl 90` before the servers start |
| `llama-primary.service` | llama-server on :8080 (Qwen2.5-3B) |
| `llama-intent.service` | llama-server on :8081 (Qwen2.5-1.5B) |

### llama-server flags used

`-ngl 99` (all layers on GPU) · `-c 4096` (context) · `-np 2` (2 parallel slots)
· `-fa on` (flash attention) · `-t 4` (CPU threads) · `--host 0.0.0.0` (LAN) ·
`--alias <name>` (model name in `/v1/models`).

### Ordering-cycle gotcha (important)

`gpu-powercap` must run before the servers but **must not** be
`After=multi-user.target` while `WantedBy=multi-user.target` — that is a cycle,
and systemd silently deletes the jobs to break it (nothing auto-starts after a
reboot). The shipped unit uses `After=systemd-modules-load.service` +
`Before=llama-primary.service llama-intent.service` instead. Verify:

```bash
systemd-analyze verify gpu-powercap.service llama-primary.service llama-intent.service
```

## 4. Operations

```bash
systemctl status gpu-powercap llama-primary llama-intent   # state
journalctl -u llama-primary -f                             # live logs (8080)
sudo systemctl restart llama-primary                       # restart 8080
curl -s localhost:8080/health ; curl -s localhost:8081/health
nvidia-smi -q -d POWER | grep 'Power Limit'                # confirm 90 W cap
```

### Swap the model on a port

Edit the unit's `ExecStart -m` path (models in `/home/pi/comfy/models/llm/`),
then `sudo systemctl daemon-reload && sudo systemctl restart llama-primary`.

### Ad-hoc model on another port

```bash
/home/pi/llama.cpp/build/bin/llama-server \
  -m /home/pi/comfy/models/llm/Llama-3.2-3B-Instruct-Q4_K_M.gguf \
  --host 0.0.0.0 --port 8082 -ngl 99 -c 4096 -np 2 -fa on -t 4 --alias llama3.2-3b
```

### Free the GPU for image generation

The ComfyUI int8 image path needs the full 8 GB. Stop the LLM services first
(`scripts/qwen-image/comfyui-serve.sh` does this), then restart them after:
`sudo systemctl start llama-primary llama-intent`.

## Stability & safety

- **Vulkan only.** CUDA/ollama hard-crash the Pi under sustained load (#2).
- **90 W cap + small VRAM-resident models** keep it inside the power envelope.
  Soak-tested: 10+ sustained generations, no crash.
- **CPU fallback** if the GPU ever misbehaves mid-use: add `-ngl 0` to the
  `ExecStart` (slower ~8 tok/s, zero GPU = zero #2 risk), daemon-reload, restart.
- **Power-on order:** GPU/ATX first → Pi second.
- **LAN-only.** Bind is `0.0.0.0` for the LAN — do not port-forward 8080/8081.
  To lock to one client, add `--api-key SECRET` to `ExecStart` and send
  `Authorization: Bearer SECRET`.

## Measured performance

| Port | Model | Throughput |
|---|---|---|
| 8080 | Qwen2.5-3B-Instruct Q4_K_M | ~75 tok/s |
| 8081 | Qwen2.5-1.5B-Instruct Q4_K_M | ~140 tok/s |

Measured on the 4060 at the 90 W cap, `-fa on`, fully VRAM-resident.

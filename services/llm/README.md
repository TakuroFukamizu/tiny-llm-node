# services/llm — LLM API backend (llama.cpp + Vulkan)

systemd services that serve small quantized LLMs over the LAN as an
OpenAI-compatible API. Full guide: [docs/llm_serving.md](../../docs/llm_serving.md).
Client/Unity API: [docs/game_ai_backend.md](../../docs/game_ai_backend.md).

## Quick start

```bash
./build-llama-cpp.sh     # build llama.cpp with -DGGML_VULKAN=ON
./download-models.sh     # fetch the Q4_K_M GGUFs into /home/pi/comfy/models/llm
./install.sh             # install + enable the 3 systemd units, verify no cycle
```

## Files

| File | What |
|---|---|
| `gpu-powercap.service` | oneshot 90 W cap before the servers (brownout mitigation, issue #2) |
| `llama-primary.service` | llama-server :8080, Qwen2.5-3B |
| `llama-intent.service` | llama-server :8081, Qwen2.5-1.5B |
| `build-llama-cpp.sh` | Vulkan build of llama.cpp |
| `download-models.sh` | download the GGUFs |
| `install.sh` | install/enable the units (checks the ordering cycle) |

Paths in the unit files assume this node's layout (`/home/pi/llama.cpp`,
`/home/pi/comfy/models/llm`). Edit them if yours differ.

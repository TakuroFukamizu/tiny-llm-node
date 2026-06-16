# Performance

## Measured

Raspberry Pi 5 (8GB) + RTX 4060, PCIe Gen 2 x1, ollama, measured 2026-06-12.

| Model | Quant | Generation | Prompt eval | Notes |
|---|---|---|---|---|
| qwen3:8b | Q4 | **43 tok/s** | ~615 tok/s | warm (model already resident in VRAM) |

Measured with `ollama run qwen3:8b --verbose`. See [software_setup.md](software_setup.md) for the full setup.

### Caveats

- **Cold start is slow.** Loading an 8B (~5.2 GB) model the first time takes a couple of minutes: the weights cross the PCIe Gen 2 x1 link (~1 GB/s) once, and CUDA graphs compile. Subsequent loads take seconds. Always benchmark on the second run or later.
- **VRAM-resident only.** The 43 tok/s figure assumes the whole model fits in the 8 GB VRAM (`ollama ps` shows `100% GPU`). Models that spill to CPU offload cross the x1 link every token and slow down dramatically.
- **For this VRAM-resident ollama run, the PCIe x1 link affects load time, not steady-state inference.** Once weights and active layers stay in VRAM, inference is bound by the GPU's memory bandwidth (272 GB/s on the RTX 4060), not the PCIe link. CPU offload, long-context memory pressure, or benchmarks that include model loading can still expose the x1 link.

## Notes on model sizing

With 8 GB VRAM, 8B-class models at Q4 are the sweet spot — they fit entirely in VRAM. Larger models (13B+) require CPU offload, which the slow PCIe x1 link makes impractical for interactive use.

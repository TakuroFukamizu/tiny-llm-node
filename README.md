# tiny-llm-node

**Language:** English | [日本語](README_ja.md)

Ultra-compact GPU LLM node powered by Raspberry Pi 5 and USB-PD.

tiny-llm-node is a portable local LLM inference node designed to run modern open-weight models on a small, power-efficient GPU using Raspberry Pi 5 as the host.

The goal is to create a:

- small
- affordable
- portable
- low power
- modular

LLM execution environment.

---

## Concept

Run local LLMs on a tiny GPU node powered only by USB-PD.

This project explores a minimal architecture:

- Raspberry Pi 5 as PCIe host
- External GPU via PCIe
- USB-PD as the only power source
- Efficient DC-DC power design
- Fully local inference

---

## Architecture Overview

```
USB-PD 3.2 power source
↓
PD trigger (28V)
↓
DC-DC converter (28V → 12V)
↓
X1010 PCIe expansion board
├── Raspberry Pi 5
└── NVIDIA RTX GPU
```

---

## Goals

- portable LLM node
- minimal power consumption
- simple hardware configuration
- reproducible build
- modular architecture

---

## Planned Model Targets

- Gemma 7B
- Mistral 7B
- Llama3 8B
- Qwen 7B

---

## Documentation

See docs directory for details:

- architecture
- hardware design
- power design
- bill of materials
- assembly guide
- software setup
- benchmarks

---

## Status

Prototype design phase.

---

## Repository Structure

```
docs/
architecture.md
hardware.md
power.md
bill_of_materials.md
assembly.md
software_setup.md
```


---

## License

MIT

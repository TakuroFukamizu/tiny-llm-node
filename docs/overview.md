# Overview

tiny-llm-node is a compact GPU-based local LLM inference node built using Raspberry Pi 5.

The design goal is to minimize system complexity while maintaining sufficient performance for modern small language models.

---

## Motivation

Modern LLMs can run efficiently on consumer GPUs.

By combining:

- Raspberry Pi 5 PCIe capability
- small GPU
- USB-PD power delivery

we can build a portable and efficient inference node.

---

## Design Principles

### GPU-first architecture

The GPU performs nearly all heavy computation.

The host system requirements are minimal.

### USB-PD powered

Use USB-PD as a universal and portable power source.

### minimal parts

Avoid unnecessary adapters where possible.

### reproducibility

All components should be easy to obtain.

---

## Expected Use Cases

- local AI assistant
- edge inference node
- offline AI environment
- portable AI demo system
- clustered small LLM nodes
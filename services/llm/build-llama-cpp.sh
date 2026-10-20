#!/bin/bash
# Build llama.cpp with the Vulkan backend on the Pi 5 + RTX 4060 node.
#
# Vulkan — NOT CUDA — is the only GPU path proven stable here: sustained CUDA
# compute sags the shared power rail and hard-resets the Pi (issue #2). The
# NVIDIA driver (580.95.05) provides the Vulkan ICD, so no CUDA toolkit / nvcc
# is needed. See docs/llm_serving.md.
set -e

SRC=/home/pi/llama.cpp

# Toolchain + Vulkan headers/loader (driver supplies the ICD).
sudo apt-get update
sudo apt-get install -y git cmake build-essential libvulkan-dev vulkan-tools glslc

# Confirm the 4060 is visible to Vulkan (expect "NVIDIA GeForce RTX 4060", not V3D).
vulkaninfo --summary 2>/dev/null | grep -i -A1 deviceName || true

if [ ! -d "$SRC" ]; then
  git clone https://github.com/ggml-org/llama.cpp "$SRC"
fi
cd "$SRC"

cmake -B build -DGGML_VULKAN=ON -DCMAKE_BUILD_TYPE=Release
cmake --build build -j"$(nproc)" --target llama-server llama-cli

echo
echo "Built: $SRC/build/bin/llama-server"
"$SRC"/build/bin/llama-server --version 2>&1 | head -3

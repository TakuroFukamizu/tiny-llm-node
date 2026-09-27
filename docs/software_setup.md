# Software Setup

**Language:** English | [日本語](software_setup_ja.md)

Complete procedure for setting up the NVIDIA driver and LLM runtime on Raspberry Pi 5 from scratch.

This guide is based on an actual build completed on 2026-06-12 (Pi 5 8GB + RTX 4060), including the failure modes encountered and how to avoid them.

---

## Verified configuration (frozen)

The userspace driver and kernel modules must match versions **exactly**. If you install the optional CUDA Toolkit, use the toolkit version called out in the LLM runtime section for this frozen configuration and do not let it replace the driver.

| Component | Version | Notes |
|---|---|---|
| OS | Raspberry Pi OS 13 (Trixie) 64-bit | Lite is fine (headless operation) |
| Kernel | `kernel8.img` (**4KB pages**) | The default 16K kernel (`kernel_2712.img`) does **not** work |
| NVIDIA userspace driver | **580.95.05** (aarch64) | Last release with an ARM userspace as of early 2026 |
| Kernel modules | [mariobalanica/open-gpu-kernel-modules](https://github.com/mariobalanica/open-gpu-kernel-modules) branch `non-coherent-arm-fixes` | Source of [NVIDIA PR #972](https://github.com/NVIDIA/open-gpu-kernel-modules/pull/972) |
| Inference runtime | ollama | Bundles its own CUDA runtime — no CUDA Toolkit needed |

> Before starting, check whether PR #972 has been merged and whether a newer driver ships an ARM userspace. If the situation is unchanged (still open as of June 2026), use the versions above. Never use a combination without a confirmed working report.

---

## 0. Prerequisites

- Hardware assembled per [assembly.md](assembly.md) and [hardware.md](hardware.md)
- **Power-on order: GPU side first, then Pi 5.** The GPU must be powered during PCIe link training
- SSH access to the Pi (display output from the NVIDIA GPU does not work — headless only; the Pi's own HDMI works as usual)

---

## 1. OS install and packages

Write Raspberry Pi OS (64-bit, Trixie) with Raspberry Pi Imager. Enable SSH in the Imager customization.

```bash
sudo apt update
sudo apt install -y build-essential git linux-headers-rpi-v8 pciutils wget curl ca-certificates
```

`linux-headers-rpi-v8` provides headers for the 4K-page kernel that the NVIDIA driver requires.

On Raspberry Pi OS the kernel headers are **split across two packages** (arch-specific `linux-headers-*-rpi-v8` + shared `linux-headers-*-common-rpi`). Installing `linux-headers-rpi-v8` pulls in both. This split matters later — see the build step warning.

---

## 2. Boot configuration

Append to `/boot/firmware/config.txt` (under the `[all]` section):

```ini
# External PCIe connector for the GPU
dtparam=pciex1
dtparam=pciex1_gen=2

# 4K page kernel — REQUIRED by the NVIDIA driver
kernel=kernel8.img
```

```bash
sudo reboot
```

Notes:

- The NVIDIA driver's memory manager only supports 4K pages. The Pi 5 default 16K kernel fails with `Cannot initialize GSP firmware RM`
- `pciex1_gen=3` gives ~2× transfer bandwidth and faster model loading, but Gen 3 is unofficial on Pi 5. Our build runs Gen 2 for stability. Start with Gen 2; try Gen 3 once everything works
- Idle link speed reading 2.5GT/s in `lspci` is normal power management (downtraining), not a fault

After reboot, verify:

```bash
getconf PAGE_SIZE        # => 4096 (if 16384, check kernel= line placement)
uname -r                 # => ends in -v8 (not -2712)
lspci -nn | grep -i nvidia
# => 0001:01:00.0 VGA compatible controller [0300]: NVIDIA Corporation AD107 [GeForce RTX 4060] [10de:2882]
```

If `lspci` shows nothing, stop and fix hardware first: cable seating/orientation, GPU aux power, power-on order, the `dtparam=pciex1` line.

---

## 3. NVIDIA userspace driver

```bash
mkdir -p ~/nvidia && cd ~/nvidia
wget https://us.download.nvidia.com/XFree86/aarch64/580.95.05/NVIDIA-Linux-aarch64-580.95.05.run
sha256sum NVIDIA-Linux-aarch64-580.95.05.run   # compare with NVIDIA's published checksum
sh ./NVIDIA-Linux-aarch64-580.95.05.run --check   # verify internal integrity
sudo sh ./NVIDIA-Linux-aarch64-580.95.05.run --no-kernel-modules --silent
```

`--check` only validates the runfile's internal archive; compare the `sha256sum` against the value published on the [NVIDIA Unix Driver Archive](https://www.nvidia.com/en-us/drivers/unix/) page to confirm the download itself is genuine.

`--no-kernel-modules` is the core of this whole procedure: install only the userspace (libcuda, nvidia-smi, nvidia-modprobe, …) and build the patched kernel modules separately in the next step.

Expected warnings (harmless): "will not install any kernel modules", "Unable to determine the path to install the libglvnd EGL vendor library config files".

---

## 4. Patched kernel modules

```bash
cd ~/nvidia
git clone --branch non-coherent-arm-fixes --depth 1 https://github.com/mariobalanica/open-gpu-kernel-modules.git
cd open-gpu-kernel-modules
head -2 version.mk    # confirm NVIDIA_VERSION = 580.95.05 (must match userspace)

make modules -j$(nproc)
sudo make modules_install
sudo depmod -a
```

Build time on a Pi 5 is roughly 30–60 minutes. The `Warning: modules_install: missing 'System.map' file. Skipping depmod.` message is fine — that is why we run `depmod -a` explicitly.

> ⚠️ **Do NOT pass `SYSSRC=/lib/modules/$(uname -r)/build` to make.** Because of the split header packages on Raspberry Pi OS, pointing `SYSSRC` at the arch-specific directory hides the shared headers from the build's feature-detection step (conftest). Every header probe then fails and the build dies with hundreds of `fatal error: stdarg.h: No such file or directory`. With no `SYSSRC` at all, the Makefile resolves the `/lib/modules/<ver>/source` (shared) and `build` (arch) symlinks correctly on its own.
>
> If you ever need to build for a kernel other than the running one, pass **only** `KERNEL_UNAME=<target version>` (e.g. `KERNEL_UNAME=6.12.75+rpt-rpi-v8`). This is also how you can pre-build the modules while still running the 16K kernel, collapsing the procedure to a single reboot. In that case run `sudo depmod -a <target version>` too — a bare `depmod -a` only regenerates dependencies for the running kernel, not the one you built for.
>
> If a build failed half-way and you are retrying, clean the stale feature-detection state first:
> ```bash
> cd kernel-open && rm -rf conftest && find . -name '*.o' -delete && find . -name '.*.cmd' -delete && cd ..
> ```

Make the modules load at boot, and pin the kernel so an `apt upgrade` can't silently break the driver:

```bash
printf 'nvidia\nnvidia-uvm\n' | sudo tee /etc/modules-load.d/nvidia.conf
sudo apt-mark hold linux-image-rpi-v8 linux-headers-rpi-v8
sudo reboot
```

(If you later unhold and update the kernel, rebuild the modules: section 4 again.)

---

## 5. Verify the driver

```bash
lsmod | grep nvidia                       # nvidia and nvidia_uvm should be loaded
sudo dmesg | grep -iE 'nvrm|nvidia' | tail -20
nvidia-smi
```

Success looks like:

```
| NVIDIA GeForce RTX 4060   Off | 00000001:01:00.0 Off | ... 8188MiB ... |
```

dmesg notes:

- `NVRM: loading NVIDIA UNIX Open Kernel Module ... 580.95.05` → good
- `nvidia_modeset` may stay unloaded on a headless CUDA-only node; `nvidia` and `nvidia_uvm` are the important modules for ollama
- `NVRM: Chipset not recognized (vendor ID 0x14e4, device ID 0x2712)` and "has not been qualified on this platform" → **harmless**, refers to the Broadcom host bridge
- `Cannot initialize GSP firmware RM` → bad: you are on the 16K kernel, or an unpatched module got loaded (see troubleshooting)

---

## 6. LLM runtime (ollama)

```bash
# Download first so you can inspect what runs as root, then execute
curl -fsSL https://ollama.com/install.sh -o ollama-install.sh
less ollama-install.sh    # optional: review before running
sh ollama-install.sh
```

The install log must end with `NVIDIA GPU installed.` (If it says CPU-only, the driver isn't visible to ollama.)

```bash
# Models that fit an 8GB VRAM GPU
ollama pull qwen3:8b        # ~5.2 GB
ollama run qwen3:8b --verbose "Introduce yourself"
```

Verify GPU offload:

```bash
ollama ps      # PROCESSOR column must say "100% GPU"
nvidia-smi     # ollama process holding ~5.5 GB VRAM
```

Operational notes:

- **The first response after a cold start is slow** (couple of minutes for an 8B model): the model crosses the PCIe x1 link once, and CUDA graphs get compiled. Subsequent loads take seconds and inference runs at full speed. Always measure performance on the second run or later
- Keep models inside VRAM. CPU-offloaded layers cross the x1 link every token and destroy throughput. On 8GB VRAM, 8B-class Q4 models are the sweet spot
- CUDA Toolkit is **not** required for ollama. Install it only if you want to build llama.cpp with CUDA yourself. For this frozen driver stack, use CUDA Toolkit 13.0.2 (`linux_sbsa` runfile) and **uncheck "Driver"** in the component selection

Measured reference (RTX 4060, qwen3:8b Q4, warm): **43 tok/s generation, ~615 tok/s prompt eval**.

---

## 7. Maintenance rules

1. The driver stack is frozen at 580.95.05. Do not update userspace, modules, or CUDA independently — they break unless versions match exactly
2. Kernel packages are held. After any intentional kernel update, rebuild and reinstall the modules (section 4). Holding also pauses kernel security updates, so plan a deliberate kernel update + module rebuild periodically rather than running indefinitely on an unpatched kernel
3. Power sequencing: GPU power first on startup, last off on shutdown
4. This is a community-patched, unofficial configuration — treat it as a lab/dev node, not production

---

## 8. WiFi provisioning by QR code (optional)

**Not verified on hardware as of 2026-09-26** — desk design only, unlike the sections above.

An M5Stack Unit QRCode connected to the Pi's I2C GPIO pins lets the headless node join a WiFi network by holding up a phone's WiFi share QR code; the `wifi-qr` daemon parses it and creates a NetworkManager profile. Wiring is in [hardware.md](hardware.md). See [services/wifi-qr/README.md](../services/wifi-qr/README.md) for the overview and [services/wifi-qr/RUNBOOK.md](../services/wifi-qr/RUNBOOK.md) for the step-by-step on-device setup (written for Claude Code to execute).

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `fatal error: stdarg.h: No such file or directory` ×many during build | `SYSSRC` was passed, breaking conftest header detection on Pi OS split headers | Rebuild without `SYSSRC` (use `KERNEL_UNAME=` only if cross-building); clean `kernel-open/conftest` first |
| `Cannot initialize GSP firmware RM` in dmesg | 16K kernel still active, or unpatched module loaded | `getconf PAGE_SIZE` must be 4096; `modinfo nvidia \| grep filename` to check which module is resolved |
| `nvidia-smi` → `No devices were found` | Driver loaded but the GPU did not initialize; possible PCIe, GSP, device node, or userspace/module mismatch | Check `lspci`, `dmesg`, `cat /proc/driver/nvidia/version`, `modinfo nvidia \| grep filename`, and `/dev/nvidia*` in that order |
| Module build fails on headers | Headers don't match target kernel | `uname -r` vs `/lib/modules/`; reinstall `linux-headers-rpi-v8` |
| GPU disappears / PCIe errors under load | Gen 3 link instability | Set `dtparam=pciex1_gen=2` |
| `lspci` shows nothing | Hardware: cabling, GPU power, power-on order | See section 0 and [troubleshooting.md](troubleshooting.md) |
| ollama installs as CPU-only | Driver not working | Fix section 5 first, then rerun the install script |

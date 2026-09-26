# Hardware

This document lists the hardware components used in tiny-llm-node.

---

# System overview

tiny-llm-node is composed of:

- Raspberry Pi 5 (host)
- PCIe expansion board (X1010)
- NVIDIA GPU
- USB-PD power supply
- DC-DC converter

---

# Host

## Raspberry Pi 5

https://www.raspberrypi.com/products/raspberry-pi-5/

![Raspberry Pi 5](https://assets.raspberrypi.com/static/1d8b0e1f1b0d4c9a87f0c0e96bbba7c2/7f6c1/hero.webp)

Role:

- PCIe host
- runs inference software
- minimal CPU requirement

Initial configuration:

- 8GB RAM model

May upgrade to:

- 16GB RAM model

---

# PCIe expansion

## Geekworm X1010 PCIe board

https://wiki.geekworm.com/X1010
https://suptronics.com/Raspberrypi/Interface/x1010-v1.1.html

![X1010](https://wiki.geekworm.com/images/6/6e/X1010.jpg)

Role:

- converts Raspberry Pi PCIe FFC to PCIe slot
- enables GPU connection
- supplies power to Raspberry Pi

Interface:

PCIe Gen3 x1 (electrical)

---

# GPU

## NVIDIA RTX 4060

Example model:

https://www.nvidia.com/en-us/geforce/graphics-cards/40-series/rtx-4060/

![RTX 4060](https://www.nvidia.com/content/dam/en-zz/Solutions/geforce/ada/rtx-4060/geforce-rtx-4060-og-image.jpg)

Selection criteria:

- low power consumption
- good tensor performance
- compact size

Target VRAM:

8GB

Supported models:

- Gemma 7B
- Mistral 7B
- Llama3 8B
- Qwen 7B

---

# Power delivery

## USB-PD trigger module

https://ja.aliexpress.com/item/1005007801369160.html

Role:

- negotiate USB-PD voltage
- outputs 28V

Requirement:

USB PD 3.1 or newer

28V 5A support

---

## DC-DC converter

Elecbee 200W buck converter

https://www.elecbee.com/en/product-detail/dc-dc-15a-200w-60v-step-down-converter-buck-board-adjustable-voltage-module-stabilized-synchronous-rectification_70580

Role:

- convert 28V to 12V
- supply power to GPU and X1010

Specification:

input:

8-60V

output:

12V

power:

200W

---

# Power supply

## USB-PD 140W power adapter

Requirement:

USB PD 3.1

28V output support

Example:

MacBook Pro 140W charger

---

# Cable

## USB-C EPR cable

Requirement:

5A rated cable

supports 140W

---

# Mechanical

## GPU support bracket

Required to prevent stress on PCIe slot.

---

# Cooling

## Raspberry Pi active cooler

recommended for stability.

## airflow

ensure GPU cooling clearance.

---

# WiFi provisioning (optional)

## M5Stack Unit QRCode (STM32F030)

Desk spec, not yet verified on hardware (2026-09-26).

https://www.switch-science.com/products/9508
https://github.com/m5stack/M5Unit-QRCode

Role:

- reads WiFi share QR codes from a smartphone
- lets the headless node join a WiFi network without keyboard or display
- driven by the `wifi-qr` daemon (see [services/wifi-qr/README.md](../services/wifi-qr/README.md))

Interface:

I2C, address 0x21

3.3V logic (I2C pull-ups to 3V3), 5V power over Grove

set the unit's slide switch to I2C (not UART)

Wiring (Grove → Pi 40-pin header):

| Grove wire | Signal | Pi header | GPIO |
|---|---|---|---|
| red | 5V | pin 2 | — |
| black | GND | pin 6 | — |
| yellow | SDA | pin 3 | GPIO2 |
| white | SCL | pin 5 | GPIO3 |

Wire colors follow the M5Stack Grove convention; go by the Grove connector pin position if your cable differs.

Requires a Grove to female jumper cable. Connects directly to the Pi GPIO, no level shifter needed.

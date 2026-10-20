#!/bin/bash
# Install the LLM API backend as systemd services (auto-start on boot,
# auto-restart on crash, 90 W GPU cap applied first).
#
# Prereqs: llama.cpp built (./build-llama-cpp.sh) and models downloaded
# (./download-models.sh). Paths inside the unit files assume this node's
# layout (/home/pi/llama.cpp, /home/pi/comfy/models/llm); edit the units if
# yours differ.
#
# Ordering note: gpu-powercap must NOT be ordered After=multi-user.target while
# WantedBy=multi-user.target — that forms a cycle and systemd silently drops the
# jobs, so nothing auto-starts. The shipped units use
# After=systemd-modules-load.service + Before=llama-* instead. Verify with
# `systemd-analyze verify`.
set -e

cd "$(dirname "$0")"

for u in gpu-powercap.service llama-primary.service llama-intent.service; do
  sudo cp "$u" /etc/systemd/system/"$u"
  echo "installed /etc/systemd/system/$u"
done

sudo systemctl daemon-reload
systemd-analyze verify gpu-powercap.service llama-primary.service llama-intent.service \
  && echo "unit ordering OK (no cycle)"

sudo systemctl enable --now gpu-powercap.service llama-primary.service llama-intent.service

echo
systemctl --no-pager status gpu-powercap llama-primary llama-intent | grep -E 'Loaded|Active' || true
echo
echo "APIs:  http://$(hostname -I | awk '{print $1}'):8080  (3B)   http://$(hostname -I | awk '{print $1}'):8081  (1.5B)"
echo "Check: curl -s localhost:8080/health ; curl -s localhost:8081/health"

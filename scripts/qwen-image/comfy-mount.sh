#!/bin/bash
IMG=/media/pi/SUNEAST/comfyui-ext4.img; MNT=/home/pi/comfy
mountpoint -q "$MNT" && { echo "already mounted"; exit 0; }
[ -f "$IMG" ] || { echo "image not found (SSD mounted at /media/pi/SUNEAST?)"; exit 1; }
sudo mount -o loop "$IMG" "$MNT" && sudo chown pi:pi "$MNT" && echo "mounted $MNT"

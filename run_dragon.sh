#!/bin/bash

sleep 10 

DRAGON_DIR="/home/dragon/Documents/dragon/Dragon-Teather-COM"
VENV_PYTHON="$DRAGON_DIR/venv/bin/python"

sudo ip addr flush dev eth0
sudo ip addr add 192.168.208.10/24 dev eth0
sudo ip link set eth0 up


"$VENV_PYTHON" "$DRAGON_DIR/dragon_pi_sender.py" \
    > "$DRAGON_DIR/output.txt" \
    2> "$DRAGON_DIR/error.txt"
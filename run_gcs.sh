sudo ip addr flush dev enx00e04c3600c7
sudo ip addr add 192.168.208.1/24 dev enx00e04c3600c7
sudo ip link set enx00e04c3600c7 up

echo "$(ip addr)"
# source Documents/dragon/Dragon-Teather-COM/venv/bin/activate
# python Documents/dragon/Dragon-Teather-COM/dragon_pi_sender.py
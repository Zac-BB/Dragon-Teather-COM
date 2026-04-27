sudo ip addr flush dev eth0
sudo ip addr add 192.168.208.10/24 dev eth0
sudo ip link set eth0 up

source ~/Documents/dragon/Dragon-Teather-COM/venv/bin/activate
python ~/Documents/dragon/Dragon-Teather-COM/dragon_pi_sender.py > output.txt 2> error.txt


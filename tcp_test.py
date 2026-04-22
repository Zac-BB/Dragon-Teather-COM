import socket
import struct

HOST = "192.168.208.10"
PORT = 5000

conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
conn.connect((HOST, PORT))
print("Connected to", HOST, PORT)

while True:
    # Receive sensor data
    data = conn.recv(12)
    if not data:
        break
    
    f1, f2, f3 = struct.unpack('fff', data)
    print("Sensor:", f1, f2, f3)

    # Send back control values
    c1, c2, c3 = 9.0, 8.0, 7.0
    conn.sendall(struct.pack('fff', c1, c2, c3))
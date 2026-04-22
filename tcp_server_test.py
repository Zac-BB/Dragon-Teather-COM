import socket
import struct

HOST = "0.0.0.0"
PORT = 5000

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind((HOST, PORT))
server.listen(1)
print(f"Listening on port {PORT}...")

conn, addr = server.accept()
print("Connected:", addr)

while True:
    data = conn.recv(12)
    if not data:
        break

    f1, f2, f3 = struct.unpack('fff', data)
    print("Sensor:", f1, f2, f3)

    c1, c2, c3 = 9.0, 8.0, 7.0
    conn.sendall(struct.pack('fff', c1, c2, c3))

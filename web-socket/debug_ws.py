import socket
import sys

def test_handshake(ip, port):
    print(f"Connecting to raw TCP socket at {ip}:{port}...")
    try:
        s = socket.create_connection((ip, port), timeout=10)
        print("Connected! Sending HTTP upgrade request headers...")
        
        request = (
            "GET / HTTP/1.1\r\n"
            f"Host: {ip}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        s.sendall(request.encode('utf-8'))
        print("Sent! Waiting for response...")
        
        response = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                print("Socket closed by remote host.")
                break
            response += chunk
            print(f"Received {len(chunk)} bytes: {chunk.decode('utf-8', errors='ignore')}")
            if b"\r\n\r\n" in response or len(response) > 1000:
                break
        s.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    ip = "192.168.0.246"
    port = 81
    if len(sys.argv) > 1:
        ip = sys.argv[1]
    test_handshake(ip, port)

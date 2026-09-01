import socket

ESP  = ("192.168.4.1", 4210)
PORT = 4210

class Link:
    def __init__(self):
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s.bind(("", PORT))
        self.s.settimeout(1.0)
        self.buf = b""
        self.s.sendto(b"hello", ESP)      # register this PC

    def readline(self):
        while b"\n" not in self.buf:
            try:
                data, _ = self.s.recvfrom(512)
                self.buf += data
            except socket.timeout:
                return b""
        line, self.buf = self.buf.split(b"\n", 1)
        return line + b"\n"

    def write(self, b):
        self.s.sendto(b, ESP)

    def close(self):
        self.s.close()
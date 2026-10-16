#!/usr/bin/env python3
"""Evaluate JavaScript in Steam's SharedJSContext over the CEF debugging port Decky enables (127.0.0.1:8080).
Minimal websocket client (no extra modules).   steam_cef.py '<js expression>'   -> prints the JSON result
Used for SteamClient.Apps.* calls (e.g. adding a non-Steam shortcut without restarting Steam)."""
import base64, json, os, socket, struct, sys, urllib.request


def target(title="SharedJSContext"):
    tabs = json.load(urllib.request.urlopen("http://127.0.0.1:8080/json", timeout=3))
    return next(t["webSocketDebuggerUrl"] for t in tabs if t.get("title") == title)


def ws_connect(url):
    host_port, path = url[len("ws://"):].split("/", 1)
    host, port = host_port.split(":")
    s = socket.create_connection((host, int(port)), timeout=10)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((f"GET /{path} HTTP/1.1\r\nHost: {host_port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        resp += s.recv(1024)
    assert b" 101 " in resp.split(b"\r\n")[0], resp[:100]
    return s


def ws_send(s, text):
    data = text.encode(); mask = os.urandom(4)
    hdr = bytearray([0x81])
    n = len(data)
    if n < 126:
        hdr.append(0x80 | n)
    elif n < 65536:
        hdr.append(0x80 | 126); hdr += struct.pack(">H", n)
    else:
        hdr.append(0x80 | 127); hdr += struct.pack(">Q", n)
    s.sendall(bytes(hdr) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))


def recv_exact(s, n):
    b = b""
    while len(b) < n:
        chunk = s.recv(n - len(b))
        if not chunk:
            raise OSError("closed")
        b += chunk
    return b


def ws_recv(s):
    msg = b""
    while True:
        h = recv_exact(s, 2); fin, op, n = h[0] & 0x80, h[0] & 0x0F, h[1] & 0x7F
        if n == 126:
            n = struct.unpack(">H", recv_exact(s, 2))[0]
        elif n == 127:
            n = struct.unpack(">Q", recv_exact(s, 8))[0]
        msg += recv_exact(s, n)
        if fin:
            return msg.decode("utf-8", "replace")


def evaluate(js):
    s = ws_connect(target())
    ws_send(s, json.dumps({"id": 1, "method": "Runtime.evaluate",
                           "params": {"expression": js, "awaitPromise": True, "returnByValue": True}}))
    while True:
        r = json.loads(ws_recv(s))
        if r.get("id") == 1:
            s.close()
            return r.get("result", {})


if __name__ == "__main__":
    print(json.dumps(evaluate(sys.argv[1])))

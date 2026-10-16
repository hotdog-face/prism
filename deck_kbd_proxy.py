#!/usr/bin/env python3
"""Pop up the Steam on-screen keyboard when a text box is tapped in a web page (Chrome in Game Mode never
does this on its own; normally you need STEAM+X).

How: the Request Movies Chrome window sends requests for the NAS web apps through this little local proxy
(chosen by a PAC rule in request_movies.sh, everything else goes direct). The proxy adds one script tag to
their HTML pages. When a text field is tapped or clicked, that script calls /__deck_kbd, and the proxy runs
`steam steam://open/keyboard`. The page keeps its own origin, so logins and cookies are unaffected.

Runs as the user service deck-kbd-proxy on 127.0.0.1:5056.
"""
import http.client
import http.server
import subprocess
import threading
import time
import urllib.parse

LISTEN = ("127.0.0.1", 5056)
HOP = {"connection", "keep-alive", "proxy-connection", "proxy-authenticate", "proxy-authorization", "te",
       "trailer", "transfer-encoding", "upgrade", "content-length", "content-encoding"}

SCRIPT = b"""(function(){
var last=0;
function editable(el){
  if(!el)return false;
  if(el.isContentEditable)return true;
  if(el.tagName==='TEXTAREA')return !el.readOnly&&!el.disabled;
  if(el.tagName!=='INPUT')return false;
  var t=(el.getAttribute('type')||'text').toLowerCase();
  return ['text','search','email','password','url','tel','number'].indexOf(t)>=0&&!el.readOnly&&!el.disabled;
}
function kb(){var n=Date.now();if(n-last<1000)return;last=n;
  fetch('/__deck_kbd',{method:'POST',keepalive:true}).catch(function(){});}
// only on a real tap/click (not when a page focuses a box by itself)
document.addEventListener('pointerup',function(){
  setTimeout(function(){if(editable(document.activeElement))kb();},60);
},true);
})();"""
TAG = b'<script src="/__deck_kbd.js"></script>'

_last_open = [0.0]
_lock = threading.Lock()


def open_keyboard():
    with _lock:
        if time.time() - _last_open[0] < 1.0:
            return
        _last_open[0] = time.time()
    subprocess.Popen(["steam", "steam://open/keyboard"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Proxy(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _reply(self, code, body=b"", ctype="text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _forward(self):
        u = urllib.parse.urlsplit(self.path)
        if not u.hostname:                       # not a proxy-style request
            return self._reply(400, b"proxy only")
        path = (u.path or "/") + (("?" + u.query) if u.query else "")
        if u.path == "/__deck_kbd":
            print("keyboard requested", flush=True)
            open_keyboard()
            return self._reply(204)
        if u.path == "/__deck_kbd.js":
            print("script served", flush=True)
            return self._reply(200, SCRIPT, "application/javascript")
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        headers["Accept-Encoding"] = "identity"  # so HTML can be edited
        try:
            c = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=60)
            c.request(self.command, path, body=body, headers=headers)
            r = c.getresponse()
            data = r.read()
        except OSError as e:
            return self._reply(502, f"upstream error: {e}".encode())
        ctype = r.getheader("Content-Type", "")
        if "text/html" in ctype and TAG not in data:
            i = data.lower().find(b"</head>")
            data = data[:i] + TAG + data[i:] if i >= 0 else TAG + data
        self.send_response(r.status, r.reason)
        for k, v in r.getheaders():
            if k.lower() not in HOP:
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _forward


if __name__ == "__main__":
    http.server.ThreadingHTTPServer.daemon_threads = True
    http.server.ThreadingHTTPServer(LISTEN, Proxy).serve_forever()

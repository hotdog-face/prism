#!/usr/bin/env python3
"""Add games from your phone (2026-10-14, approved mockup): a small upload page on the home network.

Runs only while the "Add games" card is on screen (prism_screens.py addgames): the card starts serve() in a thread
and stops it when the card closes. No sign-in, so it also answers only devices on the local network (private
addresses). Each file goes straight into EmuDeck's folder for its system (picked from the file type; the page asks
when the type fits several systems), written under a temporary name and renamed when complete. Never overwrites,
never deletes. Paused below 10 GB free (prism_storage low_space). New games show up through prism_sync.
    prism_addgames.py serve      run the server by hand (Ctrl+C stops it)
"""
import glob, html, http.server, ipaddress, json, os, re, socket, socketserver, sys, threading, time, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prism_config  # noqa: E402  (paths)

PORT = 8484
LOG = f"{prism_config.PRISM}/logs/add_games.log"
SKIP_SYSTEMS = {"steam", "movies", "tv", "music", "media", "foryou", "needsalook", "desktop", "emulators", "kodi",
                "android", "ports", "windows", "epic", "chailove"}
state = {"added": [], "server": None}


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(time.strftime("%m-%d %H:%M:%S ") + msg + "\n")


def systems_files():
    cands = [f"{prism_config.ESDE}/custom_systems/es_systems.xml", f"{prism_config.ESDE_RESOURCES}/es_systems.xml"]
    return [c for c in cands if os.path.exists(c)]


def system_table():
    """{system: (full name, {extensions})}, custom systems first (they override the bundled ones)."""
    out = {}
    for f in systems_files():
        for m in re.finditer(r"<system>(.*?)</system>", open(f, errors="replace").read(), re.S):
            b = m.group(1)
            name = re.search(r"<name>([^<]+)</name>", b); full = re.search(r"<fullname>([^<]+)</fullname>", b)
            ext = re.search(r"<extension>([^<]*)</extension>", b)
            if not name or name.group(1) in out or name.group(1) in SKIP_SYSTEMS or not ext:
                continue
            exts = {e.lower() for e in ext.group(1).split() if len(e) > 1}
            out[name.group(1)] = (html.unescape(full.group(1)) if full else name.group(1), exts)
    return out


def candidates(filename, table):
    """Systems a file could be for, most likely first: the ones that already hold games of the same file type.
    Exactly one such system -> it's the answer (an .sfc goes to SNES); several -> the page asks (a .chd could be
    PlayStation, Saturn, Dreamcast...), listing those first."""
    ext = os.path.splitext(filename)[1].lower()
    hits = []
    for sysname, (full, exts) in table.items():
        d = f"{prism_config.ROMS}/{sysname}"
        if ext not in exts or not os.path.isdir(d):
            continue
        same = sum(1 for x in os.listdir(d) if x.lower().endswith(ext) and not x.startswith((".", "_")))
        hits.append((-same, full, sysname))
    hits.sort()
    have = [h for h in hits if h[0] < 0]
    if len(have) == 1:
        return [{"system": have[0][2], "name": have[0][1]}]
    return [{"system": s_, "name": f} for _, f, s_ in (have or hits)]


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Add to Prism</title>
<style>
:root{--bg:#18181b;--card:#222226;--ink:#ececee;--muted:#a3a3aa;--line:#4a4a50;--ok:#B6D351;--amber:#F6B23C;
--neon:linear-gradient(45deg,#F0436F,#F69F3C 22%,#FBE01A 42%,#B6D351 60%,#27B6D5 80%,#29ABEB);color-scheme:dark}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 "Noto Sans",system-ui,sans-serif;
padding:22px 16px 40px;max-width:560px;margin:auto}
h1{font-size:19px;margin:0 0 16px;display:flex;align-items:center;gap:9px}h1 i{width:24px;height:24px;border-radius:7px;background:var(--neon);display:block}
label.drop{display:block;border:1.5px dashed var(--line);border-radius:14px;padding:26px 12px;text-align:center;color:var(--muted);cursor:pointer}
label.drop b{color:var(--ink);display:block;font-size:15px;font-weight:600;margin-bottom:2px}label.drop.over{border-color:#27B6D5}
input[type=file]{display:none}.files{display:grid;gap:9px;margin-top:14px}.file{background:var(--card);border-radius:11px;padding:10px 12px;display:grid;gap:4px}
.fn{font-weight:500;overflow-wrap:anywhere}.to{color:var(--muted);font-size:13px;display:flex;justify-content:space-between;gap:8px;align-items:center}
.ok{color:var(--ok)}.warn{color:var(--amber)}.prog{height:4px;border-radius:2px;background:#333;overflow:hidden}.prog i{display:block;height:100%;width:0;background:var(--neon)}
select{background:#2c2c31;color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:5px 8px;font:inherit;max-width:100%}
p.note{color:var(--muted);font-size:13px;margin:18px 0 0}
</style></head><body>
<h1><i></i>Add to Prism</h1>
<label class="drop" id="drop"><b>Choose files</b>or drag them here · ROMs, discs, zips<input type="file" id="pick" multiple></label>
<div class="files" id="files"></div>
<p class="note">Files go into the right system on the Deck. New games show up in Prism within a few minutes; box art arrives overnight. This page works while the Add games card is on the Deck's screen.</p>
<script>
const files = document.getElementById("files"), drop = document.getElementById("drop"), pick = document.getElementById("pick");
function row(f){const d=document.createElement("div");d.className="file";d.innerHTML='<div class="fn"></div><div class="to"><span class="dest">Checking…</span><span class="st"></span></div>';
d.querySelector(".fn").textContent=f.name;files.prepend(d);return d}
async function add(f){const d=row(f);const r=await fetch("/check?name="+encodeURIComponent(f.name)+"&size="+f.size).then(r=>r.json()).catch(()=>({error:"The Deck isn't answering. Is the Add games card still open?"}));
 if(r.error){d.querySelector(".dest").textContent=r.error;d.querySelector(".dest").className="dest warn";return}
 if(r.systems.length===0){d.querySelector(".dest").textContent="Prism doesn't know this file type";d.querySelector(".dest").className="dest warn";return}
 if(r.systems.length===1){send(f,d,r.systems[0]);return}
 const dest=d.querySelector(".dest");dest.textContent="Which system?";dest.className="dest warn";
 const s=document.createElement("select");s.innerHTML='<option value="">Choose…</option>'+r.systems.map(x=>'<option value="'+x.system+'">'+x.name+'</option>').join("");
 d.querySelector(".st").appendChild(s);s.onchange=()=>{if(s.value){const x=r.systems.find(y=>y.system===s.value);s.remove();send(f,d,x)}}}
function send(f,d,sys){const dest=d.querySelector(".dest"),st=d.querySelector(".st");dest.textContent="→ "+sys.name;dest.className="dest";
 const bar=document.createElement("div");bar.className="prog";bar.innerHTML="<i></i>";d.appendChild(bar);
 const x=new XMLHttpRequest();x.open("PUT","/upload?system="+encodeURIComponent(sys.system)+"&name="+encodeURIComponent(f.name));
 x.upload.onprogress=e=>{if(e.lengthComputable){const p=Math.round(e.loaded*100/e.total);bar.firstChild.style.width=p+"%";st.textContent=p+" %"}};
 x.onload=()=>{bar.remove();let r={};try{r=JSON.parse(x.responseText)}catch(e){}if(x.status===200){st.textContent="Added";st.className="st ok"}else{st.textContent=r.error||"Didn't work";st.className="st warn"}};
 x.onerror=()=>{bar.remove();st.textContent="Upload stopped";st.className="st warn"};x.send(f)}
pick.onchange=()=>{[...pick.files].forEach(add);pick.value=""};
drop.ondragover=e=>{e.preventDefault();drop.classList.add("over")};drop.ondragleave=()=>drop.classList.remove("over");
drop.ondrop=e=>{e.preventDefault();drop.classList.remove("over");[...e.dataTransfer.files].forEach(add)};
</script></body></html>"""


SETUP_FIELDS = {   # Settings > Extras > "Set up on your phone"
    "plex": ("Movies & TV", [("url", "Plex server address", "http://192.168.x.x:32400"),
                             ("token", "Plex token (Plex Web: any movie > ··· > Get Info > View XML, the "
                                       "X-Plex-Token=… part of the address)", "")]),
    "overseerr": ("Request Movies", [("url", "Overseerr address", "http://192.168.x.x:5055")]),
    "backup": ("Save backups", [("ssh", "Computer to back up to (user@host; this Deck's SSH key must be accepted "
                                        "there)", "user@192.168.x.x"),
                                ("dir", "Folder on that computer", "deck-saves")]),
}

SETUP_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Set up @TITLE@</title>
<style>
:root{--bg:#18181b;--card:#222226;--ink:#ececee;--muted:#a3a3aa;--line:#4a4a50;--ok:#B6D351;--amber:#F6B23C;
--neon:linear-gradient(45deg,#F0436F,#F69F3C 22%,#FBE01A 42%,#B6D351 60%,#27B6D5 80%,#29ABEB);color-scheme:dark}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 "Noto Sans",system-ui,sans-serif;
padding:22px 16px 40px;max-width:560px;margin:auto}
h1{font-size:19px;margin:0 0 16px;display:flex;align-items:center;gap:9px}h1 i{width:24px;height:24px;border-radius:7px;background:var(--neon);display:block}
form{display:grid;gap:14px}label{display:grid;gap:5px;color:var(--muted);font-size:13px}
input{background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:10px;padding:11px 12px;font:inherit}
button{border:0;border-radius:24px;padding:12px;font:inherit;font-weight:600;color:#111;background:var(--neon)}
p.msg{margin:4px 0 0}.ok{color:var(--ok)}.warn{color:var(--amber)}
</style></head><body><h1><i></i>Set up @TITLE@</h1>
<form id="f">@FIELDS@<button type="submit">Save on the Deck</button></form><p class="msg" id="msg"></p>
<script>
document.getElementById("f").onsubmit=async e=>{e.preventDefault();const m=document.getElementById("msg");
 const r=await fetch(location.pathname+location.search,{method:"POST",body:new URLSearchParams(new FormData(e.target))}).then(r=>r.json()).catch(()=>({error:"The Deck isn't answering. Is Settings still open?"}));
 m.textContent=r.error||"Saved. You can put your phone away.";m.className="msg "+(r.error?"warn":"ok")};
</script></body></html>"""


def setup_page(key):
    title, fields = SETUP_FIELDS[key]
    rows = "".join(f'<label>{html.escape(lab)}<input id="{k}" name="{k}" placeholder="{html.escape(ph)}" '
                   f'autocomplete="off" required></label>' for k, lab, ph in fields)
    return SETUP_PAGE.replace("@TITLE@", html.escape(title)).replace("@FIELDS@", rows)


def setup_save(key, form):
    import prism_features
    get = lambda k: (form.get(k) or [""])[0].strip()
    if key == "plex":
        url, token = get("url").rstrip("/"), get("token")
        if not url.startswith("http") or len(token) < 10:
            return "That address or token doesn't look right."
        p = f"{prism_config.PRISM}/plex.json"
        with open(p, "w") as f:
            json.dump({"url": url, "token": token}, f)
        os.chmod(p, 0o600)
    elif key == "overseerr":
        url = get("url").rstrip("/")
        if not url.startswith("http"):
            return "That address doesn't look right."
        prism_config.set("overseerr_url", url)
    elif key == "backup":
        if "@" not in get("ssh") or not get("dir"):
            return "Fill in both: user@host and a folder."
        prism_config.set("backup_ssh", get("ssh")); prism_config.set("backup_dir", get("dir"))
    prism_features.set_enabled(key, True)
    state.setdefault("setup_done", []).append(key)
    log(f"set up {key} from the phone")
    return ""


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))            # no packet is sent; just picks the interface with a route
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def safe_name(name):
    name = os.path.basename(name.replace("\\", "/")).strip()
    name = re.sub(r"[\x00-\x1f/]", "", name)
    return "" if name in ("", ".", "..") or name.startswith(".") else name[:240]


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "Prism"
    table = None

    def log_message(self, *a):
        pass

    def allowed(self):
        try:
            ip = ipaddress.ip_address(self.client_address[0])
            return ip.is_private or ip.is_loopback
        except ValueError:
            return False

    def reply(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data))); self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(data)

    def do_GET(self):
        if not self.allowed():
            return self.reply(403, json.dumps({"error": "Only devices on this network can add games."}))
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        if state.get("setup") and url.path != "/setup":      # a set-up page is open: only it answers
            return self.reply(404, json.dumps({"error": "Not here."}))
        if url.path == "/":
            return self.reply(200, PAGE, "text/html; charset=utf-8")
        if url.path == "/setup" and (q.get("f") or [""])[0] in SETUP_FIELDS and state.get("setup") == q["f"][0]:
            return self.reply(200, setup_page(q["f"][0]), "text/html; charset=utf-8")
        if url.path == "/check":
            import prism_storage
            name = safe_name((q.get("name") or [""])[0])
            if not name:
                return self.reply(400, json.dumps({"error": "That file name can't be used."}))
            if prism_storage.low_space():
                return self.reply(200, json.dumps({"error": "The Deck is almost full, so new games are paused."}))
            return self.reply(200, json.dumps({"systems": candidates(name, Handler.table)}))
        self.reply(404, json.dumps({"error": "Not here."}))

    def do_POST(self):
        if not self.allowed():
            return self.reply(403, json.dumps({"error": "Only devices on this network can change settings."}))
        url = urllib.parse.urlparse(self.path)
        key = (urllib.parse.parse_qs(url.query).get("f") or [""])[0]
        if url.path != "/setup" or key not in SETUP_FIELDS or state.get("setup") != key:
            return self.reply(400, json.dumps({"error": "That page isn't open on the Deck right now."}))
        n = min(int(self.headers.get("Content-Length") or 0), 8192)
        form = urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8", "replace"))
        err = setup_save(key, form)
        self.reply(400 if err else 200, json.dumps({"error": err} if err else {"ok": True}))

    def do_PUT(self):
        if not self.allowed():
            return self.reply(403, json.dumps({"error": "Only devices on this network can add games."}))
        if state.get("setup"):
            return self.reply(400, json.dumps({"error": "Adding games isn't open on the Deck right now."}))
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        system, name = (q.get("system") or [""])[0], safe_name((q.get("name") or [""])[0])
        size = int(self.headers.get("Content-Length") or 0)
        import shutil, prism_storage
        if url.path != "/upload" or system not in Handler.table or not name or not size:
            return self.reply(400, json.dumps({"error": "Something about that upload wasn't right."}))
        if prism_storage.low_space() or shutil.disk_usage(prism_config.ROMS).free - size < 10e9:
            return self.reply(507, json.dumps({"error": "Not enough space on the Deck."}))
        folder = f"{prism_config.ROMS}/{system}"
        dest = f"{folder}/{name}"
        if os.path.exists(dest):
            return self.reply(409, json.dumps({"error": "Already on the Deck"}))
        tmp = f"{folder}/.{name}.uploading"
        left = size
        try:
            with open(tmp, "wb") as f:
                while left > 0:
                    chunk = self.rfile.read(min(1 << 20, left))
                    if not chunk:
                        raise IOError("connection closed")
                    f.write(chunk); left -= len(chunk)
            os.replace(tmp, dest)
        except Exception as e:
            try:
                os.remove(tmp)
            except OSError:
                pass
            log(f"upload failed: {system}/{name}: {e}")
            return self.reply(500, json.dumps({"error": "Upload stopped"}))
        state["added"].append(f"{system}/{name}")
        log(f"added {system}/{name} ({size / 1e6:.1f} MB) from {self.client_address[0]}")
        self.reply(200, json.dumps({"ok": True}))


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(setup=None):
    """Start the server in a thread; returns the URL to show (setup="plex" etc.: the set-up page for that extra,
    and only that page answers then)."""
    state["setup"] = setup
    Handler.table = system_table()
    srv = Server(("0.0.0.0", PORT), Handler)
    state["server"] = srv
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://{lan_ip()}:{PORT}" + (f"/setup?f={setup}" if setup else "")
    log(f"serving at {url}")
    return url


def stop():
    srv, state["server"] = state["server"], None
    if srv:
        srv.shutdown(); srv.server_close()
        log(f"stopped; added {len(state['added'])}")
    return list(state["added"])


if __name__ == "__main__":
    if sys.argv[1:] == ["serve"]:
        print(serve())
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print(stop())
    else:
        sys.exit(__doc__)

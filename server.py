"""
FRP 内网穿透管理器
将本地服务暴露到公网，支持二维码、多服务器切换、自定义域名
"""

import os
import sys
import json
import time
import shutil
import zipfile
import threading
import subprocess
import base64
import io
import requests
import socket
from flask import Flask, render_template_string, request, jsonify

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRP_DIR = os.path.join(BASE_DIR, "frp")
FRP_CONFIG = os.path.join(FRP_DIR, "frpc.ini")
FRP_EXE = os.path.join(FRP_DIR, "frpc.exe")
DATA_FILE = os.path.join(BASE_DIR, "tunnels.json")

# 免费 frp 服务器列表
FRP_SERVERS = [
    {
        "name": "freefrp.net",
        "server_addr": "frp.freefrp.net",
        "server_port": 7000,
        "token": "freefrp.net",
    },
    {
        "name": "frp.104300.xyz",
        "server_addr": "frp.104300.xyz",
        "server_port": 7000,
        "token": "free",
    },
    {
        "name": "frp.freefrp.net (备用)",
        "server_addr": "frp.freefrp.net",
        "server_port": 7000,
        "token": "freefrp.net",
    },
]

current_tunnels = []
current_server_index = 0
frp_process = None
traffic_stats = {"bytes_in": 0, "bytes_out": 0, "connections": 0}

# 自定义域名设置
custom_domain = ""

# ─── QR 码生成 ─────────────────────

def generate_qr(data):
    try:
        import qrcode as qr_lib
        qr = qr_lib.QRCode(box_size=6, border=2)
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except:
        return None


# ─── UI ───────────────────────────

HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>内网穿透管理器</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Microsoft YaHei','PingFang SC',sans-serif;background:#f5f5f5;color:#333;min-height:100vh;padding:20px}
.container{max-width:720px;margin:0 auto}
h1{font-size:22px;margin-bottom:4px;color:#1a1a1a}
.subtitle{color:#888;font-size:13px;margin-bottom:24px}
.card{background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:16px 20px;margin-bottom:14px}
.card h2{font-size:15px;font-weight:600;margin-bottom:12px;color:#1a1a1a;border-bottom:1px solid #eee;padding-bottom:8px}
label{display:block;font-size:13px;color:#666;margin-bottom:4px}
input,select{width:100%;padding:8px 10px;border:1px solid #d0d0d0;border-radius:4px;font-size:13px;margin-bottom:10px;font-family:inherit;outline:none}
input:focus,select:focus{border-color:#4a90d9}
.btn{display:inline-block;padding:8px 18px;border-radius:4px;font-size:13px;cursor:pointer;border:none;font-weight:600}
.btn-blue{background:#4a90d9;color:#fff}
.btn-blue:hover{background:#3a7bc8}
.btn-red{background:#e74c3c;color:#fff;padding:4px 12px;font-size:12px}
.btn-red:hover{background:#c0392b}
.tunnel-item{display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid #f0f0f0}
.tunnel-item:last-child{border-bottom:none}
.tunnel-url{color:#4a90d9;font-size:13px;cursor:pointer;word-break:break-all}
.tunnel-url:hover{text-decoration:underline}
.tunnel-local{color:#999;font-size:12px;margin-top:2px}
.status{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
.status-on{background:#27ae60}
.status-off{background:#e74c3c}
.stats{display:flex;gap:20px;font-size:12px;color:#888;margin-top:8px}
.qr-box{text-align:center;margin:10px 0}
.qr-box img{max-width:160px;border:1px solid #eee;border-radius:4px}
.empty{color:#aaa;font-size:13px;padding:10px 0}
.footer{text-align:center;color:#bbb;font-size:12px;margin-top:20px}
</style>
</head>
<body>
<div class="container">
    <h1>内网穿透管理器</h1>
    <p class="subtitle">把本地服务映射到公网 · 手机扫码即访问</p>

    <div class="card">
        <h2>服务器设置</h2>
        <label>穿透服务器</label>
        <select id="serverSelect" onchange="changeServer()">
            {% for s in servers %}
            <option value="{{ loop.index0 }}" {% if loop.index0 == current_server %}selected{% endif %}>{{ s.name }}</option>
            {% endfor %}
        </select>
        <label>自定义域名（可选）</label>
        <input type="text" id="customDomain" placeholder="例如 myapp.example.com" value="{{ domain }}">
        <button class="btn btn-blue" onclick="saveSettings()" style="width:100%">保存设置</button>
    </div>

    <div class="card">
        <h2>添加映射</h2>
        <label>本地端口</label>
        <input type="number" id="localPort" placeholder="你本地的服务端口，例如 8765" value="8765">
        <label>远程端口（可不填）</label>
        <input type="number" id="remotePort" placeholder="留空则自动分配">
        <button class="btn btn-blue" onclick="addTunnel()" style="width:100%">启动穿透</button>
    </div>

    <div class="card">
        <h2>当前映射列表</h2>
        <div class="stats">
            <span>📥 入站: <span id="bytesIn">0</span> KB</span>
            <span>📤 出站: <span id="bytesOut">0</span> KB</span>
            <span>🔗 连接: <span id="conns">0</span></span>
        </div>
        <div id="tunnelList"><p class="empty">暂无映射，添加一条试试</p></div>
    </div>

    <p class="footer">基于 frp 免费穿透服务 · 重启后自动恢复 · 手机扫码即用</p>
</div>

<script>
let qrCache={};

async function load(){
    let r=await fetch('/api/tunnels');
    let d=await r.json();
    let list=document.getElementById('tunnelList');
    document.getElementById('bytesIn').textContent=(d.traffic.bytes_in/1024).toFixed(1);
    document.getElementById('bytesOut').textContent=(d.traffic.bytes_out/1024).toFixed(1);
    document.getElementById('conns').textContent=d.traffic.connections;
    if(d.tunnels.length===0){
        list.innerHTML='<p class="empty">暂无映射，添加一条试试</p>';
        return;
    }
    list.innerHTML=d.tunnels.map(t=>{
        let displayUrl = d.custom_domain ? d.custom_domain : t.public_url;
        let qrHtml = '';
        if(d.qr_codes && d.qr_codes[t.local_port]){
            qrHtml = `<div class="qr-box"><img src="data:image/png;base64,${d.qr_codes[t.local_port]}" alt="QR"><br><span style="font-size:11px;color:#888;">手机扫码直接访问</span></div>`;
        }
        return `
        <div class="tunnel-item">
            <div>
                <span class="status ${d.frp_running?'status-on':'status-off'}"></span>
                <span class="tunnel-url" onclick="copy('${displayUrl}')">${displayUrl}</span>
                <div class="tunnel-local">← 指向 localhost:${t.local_port}</div>
                ${qrHtml}
            </div>
            <button class="btn btn-red" onclick="del(${t.local_port})">删除</button>
        </div>
        `;
    }).join('');
}

async function addTunnel(){
    let local=document.getElementById('localPort').value;
    let remote=document.getElementById('remotePort').value;
    let r=await fetch('/api/tunnels',{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({local_port:parseInt(local),remote_port:remote?parseInt(remote):0})
    });
    let d=await r.json();
    if(d.ok) load();
    else alert(d.error);
}

async function del(localPort){
    let r=await fetch('/api/tunnels/'+localPort,{method:'DELETE'});
    let d=await r.json();
    if(d.ok) load();
}

async function saveSettings(){
    let serverIdx=document.getElementById('serverSelect').value;
    let domain=document.getElementById('customDomain').value;
    let r=await fetch('/api/settings',{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({server_index:parseInt(serverIdx),custom_domain:domain})
    });
    let d=await r.json();
    if(d.ok) load();
}

async function changeServer(){
    // auto save on change
}

function copy(text){
    navigator.clipboard.writeText(text).then(()=>{
        alert('地址已复制: '+text);
    });
}

load();
setInterval(load,5000);
</script>
</body>
</html>
"""

# ─── 工具函数 ─────────────────────

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"


def ensure_frp():
    if os.path.exists(FRP_EXE):
        return True

    print("[*] 正在下载 frp 客户端...")
    url = "https://github.com/fatedier/frp/releases/download/v0.61.1/frp_0.61.1_windows_amd64.zip"
    os.makedirs(FRP_DIR, exist_ok=True)

    zip_path = os.path.join(FRP_DIR, "frp.zip")
    r = requests.get(url, stream=True)
    with open(zip_path, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)

    with zipfile.ZipFile(zip_path, "r") as z:
        for name in z.namelist():
            if name.endswith("frpc.exe"):
                z.extract(name, FRP_DIR)
                src = os.path.join(FRP_DIR, name)
                shutil.move(src, FRP_EXE)
                break

    os.remove(zip_path)
    return os.path.exists(FRP_EXE)


def generate_config(tunnels):
    global current_server_index, custom_domain
    server = FRP_SERVERS[current_server_index]
    config = f"""[common]
server_addr = {server['server_addr']}
server_port = {server['server_port']}
token = {server['token']}
"""
    if custom_domain:
        for t in tunnels:
            config += f"""
[tunnel_{t['local_port']}]
type = http
local_ip = 127.0.0.1
local_port = {t['local_port']}
custom_domains = {custom_domain}
"""
    else:
        for t in tunnels:
            config += f"""
[tunnel_{t['local_port']}]
type = tcp
local_ip = 127.0.0.1
local_port = {t['local_port']}
remote_port = {t['remote_port']}
"""
    with open(FRP_CONFIG, "w") as f:
        f.write(config)


def restart_frp():
    global frp_process, traffic_stats
    if frp_process:
        try:
            frp_process.terminate()
            frp_process.wait(timeout=5)
        except:
            pass

    if not current_tunnels:
        frp_process = None
        return

    generate_config(current_tunnels)
    frp_process = subprocess.Popen(
        [FRP_EXE, "-c", FRP_CONFIG],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    traffic_stats = {"bytes_in": 0, "bytes_out": 0, "connections": len(current_tunnels)}


def load_data():
    global current_tunnels, current_server_index, custom_domain
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
            current_tunnels = data.get("tunnels", [])
            current_server_index = data.get("server_index", 0)
            custom_domain = data.get("custom_domain", "")


def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump({
            "tunnels": current_tunnels,
            "server_index": current_server_index,
            "custom_domain": custom_domain,
        }, f)


# ─── API ──────────────────────────

@app.route("/")
def index():
    return render_template_string(
        HTML,
        servers=FRP_SERVERS,
        current_server=current_server_index,
        domain=custom_domain,
    )


@app.route("/api/tunnels", methods=["GET"])
def list_tunnels():
    server = FRP_SERVERS[current_server_index]
    qr_codes = {}
    for t in current_tunnels:
        url = custom_domain if custom_domain else f"{server['server_addr']}:{t['remote_port']}"
        qr = generate_qr(f"http://{url}")
        if qr:
            qr_codes[t["local_port"]] = qr

    return jsonify({
        "tunnels": [
            {
                **t,
                "public_url": f"{server['server_addr']}:{t['remote_port']}"
            }
            for t in current_tunnels
        ],
        "frp_running": frp_process is not None and frp_process.poll() is None,
        "local_ip": get_local_ip(),
        "traffic": traffic_stats,
        "qr_codes": qr_codes,
        "custom_domain": custom_domain,
    })


@app.route("/api/tunnels", methods=["POST"])
def add_tunnel():
    data = request.get_json()
    local_port = data.get("local_port")
    remote_port = data.get("remote_port", 0)

    if not local_port:
        return jsonify({"ok": False, "error": "请填写本地端口"})

    for t in current_tunnels:
        if t["local_port"] == local_port:
            return jsonify({"ok": False, "error": "该端口已映射"})

    if remote_port == 0:
        max_r = max([t["remote_port"] for t in current_tunnels], default=40000)
        remote_port = max_r + 1

    current_tunnels.append({"local_port": local_port, "remote_port": remote_port})
    save_data()
    restart_frp()
    return jsonify({"ok": True})


@app.route("/api/tunnels/<int:local_port>", methods=["DELETE"])
def remove_tunnel(local_port):
    global current_tunnels
    current_tunnels = [t for t in current_tunnels if t["local_port"] != local_port]
    save_data()
    restart_frp()
    return jsonify({"ok": True})


@app.route("/api/settings", methods=["POST"])
def save_settings():
    global current_server_index, custom_domain
    data = request.get_json()
    current_server_index = data.get("server_index", 0)
    custom_domain = data.get("custom_domain", "")
    save_data()
    restart_frp()
    return jsonify({"ok": True})


# ─── 启动 ─────────────────────────

if __name__ == "__main__":
    load_data()
    if not ensure_frp():
        print("[ERROR] frp 客户端下载失败，请检查网络")
        sys.exit(1)
    if current_tunnels:
        restart_frp()
    print(f"[*] 管理面板: http://{get_local_ip()}:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
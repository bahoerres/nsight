"""One-shot MCP query helper. Set $SQL or pipe SQL on stdin."""
from __future__ import annotations
import json, os, subprocess, sys

SQL = os.environ.get("SQL") or sys.stdin.read()
if not SQL.strip(): sys.exit("No SQL provided.")

CMD = ["ssh","-o","BatchMode=yes","-o","ConnectTimeout=10","docker-top",
       "cd /home/sysadmin/stacks/nsight && exec ./.venv/bin/python -m mcp_server"]
p = subprocess.Popen(CMD, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE, text=True, bufsize=1)
send = lambda m: (p.stdin.write(json.dumps(m)+"\n"), p.stdin.flush())
recv = lambda: json.loads(p.stdout.readline())
try:
    send({"jsonrpc":"2.0","method":"initialize","params":{
        "protocolVersion":"2025-11-25","capabilities":{},
        "clientInfo":{"name":"ask","version":"0.1"}},"id":0}); recv()
    send({"jsonrpc":"2.0","method":"notifications/initialized"})
    send({"jsonrpc":"2.0","method":"tools/call","params":{
        "name":"query_sql","arguments":{"sql":SQL.strip(),"limit":200}},"id":1})
    resp = recv()
finally:
    try: p.stdin.close()
    except Exception: pass
    p.wait(timeout=5)
if "error" in resp: sys.exit(f"ERROR: {resp['error']}")
print(resp["result"]["content"][0]["text"])

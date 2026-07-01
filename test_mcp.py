"""Fast, read-only integration smoke test: drive server.py over MCP stdio.

Verifies the server starts, lists tools, and serves live read tools. Does NOT
create/modify anything. Run:  python test_mcp.py
"""
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path

SERVER = Path(__file__).parent / "server.py"
# -u: unbuffered, so server output never stalls the pipe.
proc = subprocess.Popen([sys.executable, "-u", str(SERVER)], stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
_id = 0


def send(method, params=None, notify=False):
    global _id
    msg = {"jsonrpc": "2.0", "method": method}
    if not notify:
        _id += 1
        msg["id"] = _id
    if params is not None:
        msg["params"] = params
    proc.stdin.write(json.dumps(msg) + "\n"); proc.stdin.flush()
    if notify:
        return None
    while True:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("server closed: " + proc.stderr.read())
        line = line.strip()
        if line and (m := json.loads(line)).get("id") == _id:
            return m


def call(name, args):
    r = send("tools/call", {"name": name, "arguments": args})
    return r["result"]["content"][0]["text"] if "error" not in r else f"<error> {r['error']}"


try:
    send("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "smoke", "version": "1"}})
    send("notifications/initialized", notify=True)
    tools = [t["name"] for t in send("tools/list")["result"]["tools"]]
    print(f"TOOLS ({len(tools)}):", ", ".join(tools))
    print("\n[list_lookup priorities]\n", call("list_lookup", {"kind": "priorities"}))
    print("\n[search_solution_elements '10.05.0106']\n", call("search_solution_elements", {"query": "10.05.0106"}))
    print("\n[search_requirements 'CFIN' top 3]\n", call("search_requirements", {"query": "CFIN", "top": 3}))
    print("\n[soldoc_browse roots]\n", call("soldoc_browse", {}))
    print("\nOK: server serves live read tools over MCP stdio.")
finally:
    proc.stdin.close()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()

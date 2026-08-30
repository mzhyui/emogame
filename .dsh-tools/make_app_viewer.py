#!/usr/bin/env python3
"""Build app.py-viewer.html (窗口2 of 分析器 project) and widget-result.json,
then POST both into /home/mzhyui/git/MultiagentLongText via the dsh-worktable
write API (the sandbox cannot write that folder directly).
"""
import json
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_SRC = Path("/home/mzhyui/git/emogame/app.py")
API = "http://127.0.0.1:3080/api/worktable/write"

HTML_NAME = "app.py-viewer.html"
RESULT_NAME = "widget-result.json"

META = {
    "lines": 500,
    "bytes": 18466,
    "mtime": "2026-07-17 14:41",
}


def post(path: str, content: str) -> str:
    req = urllib.request.Request(
        API,
        data=json.dumps({"path": path, "content": content}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")


src = APP_SRC.read_text(encoding="utf-8")
snapshot = json.dumps(src, ensure_ascii=False).replace("<", "\\u003c")

template = (HERE / "app_viewer_template.html").read_text(encoding="utf-8")
html = (
    template.replace("__SNAPSHOT__", snapshot)
    .replace("__LINES__", str(META["lines"]))
    .replace("__BYTES__", f"{META['bytes'] / 1024:.1f}")
    .replace("__MTIME__", META["mtime"])
)

result = json.dumps(
    {"window": "窗口2", "path": HTML_NAME, "kind": "html"},
    ensure_ascii=False,
)

print("HTML bytes:", len(html.encode("utf-8")))
print("write html ->", post(HTML_NAME, html))
print("write result ->", post(RESULT_NAME, result))

#!/usr/bin/env python3
"""Build emogame-db-dashboard.html (窗口1 of 分析器 project) from a live query
of /home/mzhyui/git/emogame/data/emogame.db, then POST it and the updated
widget-result.json into /home/mzhyui/git/MultiagentLongText via the worktable
write API (sandbox cannot write that folder directly).
"""
import json
import os
import sqlite3
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = Path("/home/mzhyui/git/emogame/data/emogame.db")
API = "http://127.0.0.1:3080/api/worktable/write"

HTML_NAME = "emogame-db-dashboard.html"
RESULT_NAME = "widget-result.json"

WINDOW2_HTML = "app.py-viewer.html"  # 上轮已挂载的窗口2 产物，清单里保留


def post(path: str, content: str) -> str:
    req = urllib.request.Request(
        API,
        data=json.dumps({"path": path, "content": content}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")


def fmt_ts(ts: object) -> str:
    """Unix 时间戳(秒，可能为 str/float) → 本地可读时间；空值返回 '—'。"""
    if ts in (None, ""):
        return "—"
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, OSError):
        return str(ts)


conn = sqlite3.connect(str(DB))
tables = []
for name in [r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
).fetchall()]:
    cols = [
        {"name": c[1], "type": c[2]}
        for c in conn.execute(f'PRAGMA table_info("{name}")').fetchall()
    ]
    rows = [list(r) for r in conn.execute(f'SELECT * FROM "{name}"').fetchall()]
    # 时间戳列转可读格式（按列名启发式）
    for r in rows:
        for i, c in enumerate(cols):
            if c["name"] in ("created_at", "updated_at"):
                r[i] = fmt_ts(r[i])
    tables.append({"name": name, "columns": cols, "rows": rows})
conn.close()

total_rows = sum(len(t["rows"]) for t in tables)
total_cols = sum(len(t["columns"]) for t in tables)

meta = {
    "snapTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "dbPath": str(DB),
    "sizeBytes": os.path.getsize(DB),
    "mtime": datetime.fromtimestamp(os.path.getmtime(DB)).strftime("%Y-%m-%d %H:%M"),
    "totalRows": total_rows,
    "totalCols": total_cols,
    "tables": len(tables),
}
dbdata = {"meta": meta, "tables": tables}

payload_json = json.dumps(dbdata, ensure_ascii=False).replace("<", "\\u003c")

template = (HERE / "db_dashboard_template.html").read_text(encoding="utf-8")
html = (
    template.replace("__DATA__", payload_json)
    .replace("__TABLES__", str(len(tables)))
    .replace("__ROWS__", str(total_rows))
    .replace("__COLS__", str(total_cols))
    .replace("__SIZE__", f"{meta['sizeBytes'] / 1024:.1f}")
    .replace("__MTIME__", meta["mtime"])
    .replace("__SNAP_TIME__", meta["snapTime"])
)

# 产物清单：窗口1（本次）+ 窗口2（保留上轮挂载）
result = json.dumps(
    [
        {"window": "窗口1", "path": HTML_NAME, "kind": "html"},
        {"window": "窗口2", "path": WINDOW2_HTML, "kind": "html"},
    ],
    ensure_ascii=False,
)

print("tables:", [t["name"] for t in tables], "| rows:", total_rows, "| cols:", total_cols)
print("HTML bytes:", len(html.encode("utf-8")))
print("write html ->", post(HTML_NAME, html))
print("write result ->", post(RESULT_NAME, result))

#!/usr/bin/env python3
"""Build wzry-skins-dashboard.html (窗口1 of 分析器 project) from a live query
of /home/mzhyui/git/emogame/data/wzry_skins/skins.sqlite3, then POST it and
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
DB = Path("/home/mzhyui/git/emogame/data/wzry_skins/skins.sqlite3")
IMAGES_DIR = Path("/home/mzhyui/git/emogame/data/wzry_skins/images")
API = "http://127.0.0.1:3080/api/worktable/write"

HTML_NAME = "wzry-skins-dashboard.html"
RESULT_NAME = "widget-result.json"

WINDOW2_HTML = "app.py-viewer.html"  # 保留窗口2 挂载


def post(path: str, content: str) -> str:
    req = urllib.request.Request(
        API,
        data=json.dumps({"path": path, "content": content}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read().decode("utf-8")


conn = sqlite3.connect(str(DB))
cur = conn.cursor()


def q(sql: str, *args):
    return cur.execute(sql, args).fetchall()


# 表清单 + 行数
tables = [r[0] for r in q(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
)]
table_list = []
table_rows = {}
for t in tables:
    n = q(f'SELECT COUNT(*) FROM "{t}"')[0][0]
    table_rows[t] = n
    table_list.append({"name": t, "rows": n})

# skins：精简列
skins = []
for r in q("SELECT source_key, skin_name, hero_name, quality, online_date, "
           "acquire_method, price_text, has_detail_record FROM skins ORDER BY source_key"):
    skins.append({
        "source_key": r[0], "skin_name": r[1], "hero_name": r[2], "quality": r[3] or "",
        "online_date": r[4] or "", "acquire_method": r[5] or "",
        "price_text": r[6] if r[6] not in (None, "None") else "",
        "has_detail_record": str(r[7] or 0),
    })

# heroes
heroes = [{
    "hero_id": r[0], "hero_name": r[1], "title": r[2] or "", "hero_type": r[4] or "",
    "skin_count": r[5],
} for r in q("SELECT hero_id, hero_name, title, hero_type_code, hero_type, skin_count, raw_json "
             "FROM heroes ORDER BY CAST(hero_id AS INTEGER)")]

# 收入：365 天，按日期升序
revenue = [{
    "revenue_date": r[0], "game": r[1], "platform": r[2], "market": r[3],
    "currency": r[4], "estimated_revenue": r[5], "source": r[6],
} for r in q("SELECT revenue_date, game, platform, market, currency, estimated_revenue, "
             "source FROM app_revenue_daily ORDER BY revenue_date")]
revenue_total = sum(r["estimated_revenue"] for r in revenue)

# 价值记录
values = []
for r in q("SELECT source_key, period_start, period_end, sales_volume, avg_spend_cny, "
           "attributed_revenue, signed_uplift, confidence, attribution_method "
           "FROM skin_value_records ORDER BY source_key"):
    values.append({
        "source_key": r[0],
        "period": f"{r[1]} ~ {r[2]}",
        "sales_volume": r[3], "avg_spend_cny": r[4],
        "attributed_revenue": r[5], "signed_uplift": r[6],
        "confidence": r[7], "attribution_method": r[8],
    })

# 资产分布
asset_types = [{"type": r[0], "count": r[1]} for r in q(
    "SELECT asset_type, COUNT(*) FROM skin_assets GROUP BY asset_type ORDER BY 2 DESC")]
asset_statuses = [{"status": r[0], "count": r[1]} for r in q(
    "SELECT download_status, COUNT(*) FROM skin_assets GROUP BY download_status ORDER BY 2 DESC")]
by_type = {t["type"]: t["count"] for t in asset_types}

# 爬虫信息
crawl_row = q("SELECT started_at, finished_at, hero_count, skin_count, asset_count, "
              "downloaded_count, image_failed_count FROM crawl_runs ORDER BY run_id DESC LIMIT 1")
crawl = {
    "started_at": crawl_row[0][0] if crawl_row else "—",
    "hero_count": crawl_row[0][2] if crawl_row else 0,
    "skin_count": crawl_row[0][3] if crawl_row else 0,
    "asset_count": crawl_row[0][4] if crawl_row else 0,
    "downloaded_count": crawl_row[0][5] if crawl_row else 0,
    "image_failed_count": crawl_row[0][6] if crawl_row else 0,
}
conn.close()

images_count = len(list(IMAGES_DIR.iterdir())) if IMAGES_DIR.is_dir() else 0

# 表说明
DESC = {
    "app_revenue_daily": "每日估算收入（王者荣耀 iPhone CN）",
    "crawl_runs": "爬虫运行记录",
    "heroes": "英雄目录（131 名）",
    "market_signal_records": "市场信号（空）",
    "opinion_evidence_items": "舆情证据（空）",
    "revenue_import_batches": "收入导入批次",
    "skin_assets": "皮肤/英雄图片资产引用",
    "skin_value_records": "皮肤价值评估记录",
    "skins": "皮肤主表",
    "synth_invoice_batches": "合成发票批次",
    "synthetic_invoice_daily": "合成发票明细（大表）",
}
for t in table_list:
    t["desc"] = DESC.get(t["name"], "")

meta = {
    "snapTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "dbPath": str(DB),
    "sizeMB": round(os.path.getsize(DB) / 1024 / 1024, 1),
    "tables": len(tables),
    "totalRows": sum(table_rows.values()),
    "skins": table_rows.get("skins", 0),
    "heroes": table_rows.get("heroes", 0),
    "assets": table_rows.get("skin_assets", 0),
    "images": images_count,
}

dbdata = {
    "meta": meta,
    "tableList": table_list,
    "crawl": crawl,
    "skins": skins,
    "heroes": heroes,
    "revenue": revenue,
    "revenueTotal": revenue_total,
    "values": values,
    "assets": {"total": table_rows.get("skin_assets", 0), "images": images_count,
               "byType": by_type, "typeDist": asset_types, "statuses": asset_statuses},
}

payload_json = json.dumps(dbdata, ensure_ascii=False).replace("<", "\\u003c")

template = (HERE / "skins_dashboard_template.html").read_text(encoding="utf-8")
html = (
    template.replace("__DATA__", payload_json)
    .replace("__SKINS__", str(len(skins)))
    .replace("__HEROES__", str(len(heroes)))
    .replace("__REV__", str(len(revenue)))
    .replace("__VALUES__", str(len(values)))
    .replace("__SNAP_TIME__", meta["snapTime"])
)

result = json.dumps(
    [
        {"window": "窗口1", "path": HTML_NAME, "kind": "html"},
        {"window": "窗口2", "path": WINDOW2_HTML, "kind": "html"},
    ],
    ensure_ascii=False,
)

print("skins:", len(skins), "heroes:", len(heroes), "revenue:", len(revenue),
      "values:", len(values), "images:", images_count)
print("payload JSON:", round(len(payload_json) / 1024), "KB | HTML:", round(len(html.encode()) / 1024), "KB")
print("write html ->", post(HTML_NAME, html))
print("write result ->", post(RESULT_NAME, result))

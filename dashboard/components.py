"""Shared Streamlit UI components for the dashboard pages.

Keeps common patterns -- empty states, collapsible audit blocks, metric cards,
and the consistent status chip -- in one place so every page reads identically.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from dashboard.format import (
    cash_status_label,
    completeness_label,
    emotion_status_label,
)


def status_chip(label: str, kind: str = "neutral") -> str:
    """Return an HTML chip string for a small status label."""
    colors = {
        "validated": ("#065f46", "#d1fae5"),
        "missing": ("#92400e", "#fef3c7"),
        "has_record": ("#1e40af", "#dbeafe"),
        "risk": ("#b45309", "#fef3c7"),
        "neutral": ("#374151", "#f3f4f6"),
    }
    fg, bg = colors.get(kind, colors["neutral"])
    return (
        f"<span style='color:{fg};background:{bg};padding:1px 8px;"
        f"border-radius:10px;font-size:0.8rem;'>{label}</span>"
    )


def render_empty_state(message: str, workbench_link: bool = True) -> None:
    """Show a clear, non-misleading empty state (no fake rankings)."""
    st.info(message)
    if workbench_link:
        st.caption("提示：前往「数据工作台」导入证据或模拟信号后，此处才会填充数据。")


def render_audit_block(title: str, payload: dict[str, Any], source_key: str) -> None:
    """Collapsible, auditable raw JSON block."""
    with st.expander(f"{title}（原始数据，可审计）"):
        st.download_button(
            "下载 JSON",
            data=__import__("json").dumps(payload, ensure_ascii=False, indent=2),
            file_name=f"{source_key}-audit.json",
            mime="application/json",
        )
        st.json(payload)


def metric_card(label: str, value: str, caption: str | None = None) -> None:
    """Thin wrapper around st.metric for consistent styling."""
    if caption:
        st.metric(label, value, help=caption)
    else:
        st.metric(label, value)


def data_completeness(completeness: str) -> str:
    return completeness_label(completeness)


def emotion_chip(status: str) -> str:
    kind = "validated" if status == "validated" else "missing"
    return status_chip(emotion_status_label(status), kind)


def cash_chip(status: str) -> str:
    kind = "has_record" if status == "has_record" else "missing"
    return status_chip(cash_status_label(status), kind)


def note_box(text: str, kind: str = "audit") -> None:
    """Render an audit/risk note using the existing CSS classes from app.py."""
    css_class = "risk-note" if kind == "risk" else "audit-note"
    st.markdown(f"<div class='{css_class}'>{text}</div>", unsafe_allow_html=True)

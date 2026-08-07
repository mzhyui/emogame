"""数据工作台页：管理入口，保留原有导入、模拟与审计能力。

持久化操作（CSV 导入、人工现金证据）明确与只读分析页分离；模拟值仅存会话。
逻辑全部复用 dashboard/workbench_render.py，不复制任何业务逻辑。
"""

from __future__ import annotations

import streamlit as st

from dashboard.workbench_render import render_workbench


def main() -> None:
    render_workbench()


if __name__ == "__main__":
    main()

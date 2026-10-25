from __future__ import annotations

import ast
from pathlib import Path


def test_quick_page_and_worker_sources_parse_and_expose_expected_controls():
    root = Path(__file__).resolve().parents[1]
    quick = (root / "pages" / "00_Quick_Direction.py").read_text(encoding="utf-8")
    worker = (root / "auto_forecast_worker.py").read_text(encoding="utf-8")
    app = (root / "app.py").read_text(encoding="utf-8")
    start = (root / "start_dashboard.bat").read_text(encoding="utf-8")

    ast.parse(quick)
    ast.parse(worker)
    ast.parse(app)

    assert "预测记录" in quick
    assert "启用自动预测" in quick
    assert "find_existing_forecast" in quick
    assert "q-grid" in quick
    assert "auto_forecast_worker.py" in start
    assert "pythonw.exe" in start
    assert "SINGLETON_PORT = 8766" in worker


def test_app_does_not_render_old_magic_english_banner():
    root = Path(__file__).resolve().parents[1]
    app = (root / "app.py").read_text(encoding="utf-8")
    assert '"""Chinese desktop-style entrypoint' not in app

from pathlib import Path


def test_react_frontend_exposes_telegram_connection_and_report_flow() -> None:
    root = Path("frontend/src")
    panel = (root / "components/TelegramSourcesPanel.jsx").read_text()
    create = (root / "components/CreateJobPanel.jsx").read_text()
    app = (root / "App.jsx").read_text()

    assert "/telegram/connection/start" in panel
    assert "/telegram/connection/code" in panel
    assert "/telegram/connection/password" in panel
    assert "Gesammelter Chat" in create
    assert "Letzte 14 Tage" in create
    assert 'request("/jobs/telegram"' in app


def test_frontend_proxies_telegram_api_routes() -> None:
    nginx = Path("frontend/docker/nginx/default.conf.template").read_text()
    vite = Path("frontend/vite.config.js").read_text()

    assert "question-sets|telegram" in nginx
    assert '"/telegram": proxyTarget' in vite

from pathlib import Path


def test_react_frontend_exposes_telegram_connection_and_report_flow() -> None:
    root = Path("frontend/src")
    panel = (root / "components/TelegramSourcesPanel.jsx").read_text()
    create = (root / "components/CreateJobPanel.jsx").read_text()
    app = (root / "App.jsx").read_text()

    assert "/telegram/connection/start" in panel
    assert "/telegram/connection/code" in panel
    assert "/telegram/connection/password" in panel
    assert "Collected chat" in create
    assert "Use the last 14 days" in create
    assert "Open Telegram Setup" in create
    assert 'activeView === "telegram"' in app
    assert 'request("/jobs/telegram"' in app


def test_react_frontend_uses_desktop_shell_and_inline_question_set_forms() -> None:
    root = Path("frontend/src")
    sidebar = (root / "components/AppSidebar.jsx").read_text()
    question_sets = (root / "components/QuestionSetsPanel.jsx").read_text()
    progress = (root / "components/JobMonitorPanel.jsx").read_text()
    styles = (root / "styles.css").read_text()

    assert "New Analysis" in sidebar
    assert "Telegram Setup" in sidebar
    assert "Uploaded export" in sidebar
    assert "status-dot-${job.status}" in sidebar
    assert "window.prompt" not in question_sets
    assert "inline-template-form" in question_sets
    assert "Progress" in progress
    assert "progress-summary-card" in progress
    assert "min-width: 1180px" in styles


def test_frontend_proxies_telegram_api_routes() -> None:
    nginx = Path("frontend/docker/nginx/default.conf.template").read_text()
    vite = Path("frontend/vite.config.js").read_text()

    assert "question-sets|telegram" in nginx
    assert '"/telegram": proxyTarget' in vite

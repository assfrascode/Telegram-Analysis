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
    assert "Use the last 30 days" in create
    assert "Allow partial report" in create
    assert "allow_partial_telegram_sync" in create
    assert "Open Telegram Setup" in create
    assert "Scheduled reports" in panel
    assert "/telegram/report-schedules" in panel
    assert "allow_partial_telegram_sync" in panel
    assert "Scheduled:" in (root / "components/AppSidebar.jsx").read_text()
    assert 'activeView === "telegram"' in app
    assert 'request("/jobs/telegram"' in app


def test_react_frontend_exposes_external_collector_without_backend_credentials() -> None:
    root = Path("frontend/src")
    panel = (root / "components/TelegramSourcesPanel.jsx").read_text()
    create = (root / "components/CreateJobPanel.jsx").read_text()
    app = (root / "App.jsx").read_text()

    assert "External collector" in panel
    assert "No backend account required" in panel
    assert "Backend Telegram account" in panel
    assert "Show backend account setup" in panel
    assert "Request collector sync" in panel
    assert "Collector synchronization requested" in panel
    assert 'chat.ingest_mode === "external_push"' in panel
    assert "showBackendSetup" in panel
    assert "External collector" in create
    assert "Backend account" in create
    assert "unavailableBackendChats" in create
    assert "backendTelegramConnected" in create
    assert "Promise.allSettled" in app
    assert 'request("/telegram/chats")' in app


def test_react_frontend_uses_desktop_shell_and_inline_question_set_forms() -> None:
    root = Path("frontend/src")
    sidebar = (root / "components/AppSidebar.jsx").read_text()
    question_sets = (root / "components/QuestionSetsPanel.jsx").read_text()
    progress = (root / "components/JobMonitorPanel.jsx").read_text()
    styles = (root / "styles.css").read_text()

    assert "New Analysis" in sidebar
    assert "Telegram Setup" in sidebar
    assert "Uploaded export" in sidebar
    assert "`Uploaded: ${job.source_name}`" in sidebar
    assert "status-dot-${job.status}" in sidebar
    assert "window.prompt" not in question_sets
    assert "inline-template-form" in question_sets
    assert "Progress" in progress
    assert "Retry analysis" in progress
    assert "progress-summary-card" in progress
    assert "min-width: 1180px" in styles


def test_frontends_expose_failed_job_retry_action() -> None:
    root = Path("frontend/src")
    app = (root / "App.jsx").read_text()
    progress = (root / "components/JobMonitorPanel.jsx").read_text()
    static_app = Path("backend/app/static/app/app.js").read_text()
    static_html = Path("backend/app/static/app/index.html").read_text()

    assert "`/jobs/${currentJobId}/retry`" in app
    assert "job.retry.started" in app
    assert "Retry analysis" in progress
    assert "`/jobs/${state.currentJobId}/retry`" in static_app
    assert "job.retry.started" in static_app
    assert 'id="retry"' in static_html


def test_frontend_proxies_telegram_api_routes() -> None:
    nginx = Path("frontend/docker/nginx/default.conf.template").read_text()
    vite = Path("frontend/vite.config.js").read_text()

    assert "question-sets|telegram" in nginx
    assert '"/telegram": proxyTarget' in vite


def test_frontend_supports_larger_upload_path() -> None:
    react_client = Path("frontend/src/api/client.js").read_text()
    react_app = Path("frontend/src/App.jsx").read_text()
    static_app = Path("backend/app/static/app/app.js").read_text()
    compose = Path("docker-compose.yml").read_text()
    dockerfile = Path("frontend/Dockerfile").read_text()

    assert "DIRECT_OBJECT_STORE_UPLOAD_MAX_BYTES" in react_client
    assert 'xhr.open("PUT", upload.presigned_put_url)' in react_client
    assert "`/uploads/${upload.upload_id}/complete`" in react_client
    assert "uploadFileViaBackend(upload, file, token, onProgress)" in react_client
    assert "uploadFileForAnalysis(upload, file, token, setUploadProgress)" in react_app
    assert "DIRECT_OBJECT_STORE_UPLOAD_MAX_BYTES" in static_app
    assert 'xhr.open("PUT", upload.presigned_put_url)' in static_app
    assert "`/uploads/${upload.upload_id}/complete`" in static_app
    assert 'CLIENT_MAX_BODY_SIZE: "${CLIENT_MAX_BODY_SIZE:-50g}"' in compose
    assert "CLIENT_MAX_BODY_SIZE=50g" in dockerfile

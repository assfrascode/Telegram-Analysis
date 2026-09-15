from pathlib import Path


def test_frontend_download_uses_authorization_header():
    js = Path('backend/app/static/app/app.js').read_text()
    assert 'Report-Download gestartet' in js
    assert 'Authorization": `Bearer ${token}`' in js or 'Authorization": `Bearer ${token}`' in js
    assert 'fetch(`/jobs/${currentJobId}/report/${downloadPath}`' in js
    assert 'downloadKind === "complete"' in js
    assert 'id="downloadReports"' in Path('backend/app/static/app/index.html').read_text()
    assert "downloadFilenameFromResponse(res)" in js


def test_react_download_uses_content_disposition_filename():
    client = Path("frontend/src/api/client.js").read_text()
    app = Path("frontend/src/App.jsx").read_text()
    monitor = Path("frontend/src/components/JobMonitorPanel.jsx").read_text()

    assert 'response.headers.get("Content-Disposition")' in client
    assert "filename*=UTF-8" not in app
    assert "const { blob, filename } = await downloadBlob" in app
    assert "anchor.download = filename" in app
    assert 'downloadKind === "complete"' in app
    assert '`/jobs/${currentJobId}/report/${downloadPath}`' in app
    assert 'onDownload("complete")' in monitor
    assert 'onDownload("reports")' in monitor
    assert "Complete chat + files" in monitor
    assert "Main + sub-reports only" in monitor

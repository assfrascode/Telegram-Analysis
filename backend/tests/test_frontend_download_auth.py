from pathlib import Path


def test_frontend_download_uses_authorization_header():
    js = Path('backend/app/static/app/app.js').read_text()
    assert 'Report-Download gestartet' in js
    assert 'Authorization": `Bearer ${token}`' in js or 'Authorization": `Bearer ${token}`' in js
    assert 'fetch(`/jobs/${currentJobId}/report/download`' in js
    assert "downloadFilenameFromResponse(res)" in js


def test_react_download_uses_content_disposition_filename():
    client = Path("frontend/src/api/client.js").read_text()
    app = Path("frontend/src/App.jsx").read_text()

    assert 'response.headers.get("Content-Disposition")' in client
    assert "filename*=UTF-8" not in app
    assert "const { blob, filename } = await downloadBlob" in app
    assert "anchor.download = filename" in app

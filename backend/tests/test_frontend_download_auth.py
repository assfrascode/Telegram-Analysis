from pathlib import Path


def test_frontend_download_uses_authorization_header():
    js = Path('backend/app/static/app/app.js').read_text()
    assert 'Report-Download gestartet' in js
    assert 'Authorization": `Bearer ${token}`' in js or 'Authorization": `Bearer ${token}`' in js
    assert 'fetch(`/jobs/${currentJobId}/report/download`' in js

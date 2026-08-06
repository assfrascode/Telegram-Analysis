import tempfile
import zipfile
from pathlib import Path

import pytest

from app.services import report_bundle
from app.services.report_bundle import (
    ReportBundleConflictError,
    append_report_to_archive,
    build_report_bundle,
    remove_temp_file,
)


def _write_zip(path: Path, files: dict[str, bytes | str]) -> None:
    with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            data = value.encode("utf-8") if isinstance(value, str) else value
            archive.writestr(name, data)


def _report_zip(path: Path) -> None:
    _write_zip(
        path,
        {
            "report/index.html": "report home",
            "report/assets/report.css": "body {}",
            "report/questions/q_001.html": "answer",
        },
    )


def _zip_bytes(tmp_path: Path, name: str, files: dict[str, bytes | str]) -> bytes:
    path = tmp_path / name
    _write_zip(path, files)
    return path.read_bytes()


class _FakeMinio:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    def fget_object(self, bucket: str, object_key: str, path: str) -> None:
        Path(path).write_bytes(self.objects[object_key])


def test_bundle_adds_report_to_root_export_and_preserves_original_files(tmp_path: Path) -> None:
    bundle_path = tmp_path / "original.zip"
    report_path = tmp_path / "report.zip"
    original_files = {
        "result.json": '{"name":"Root chat","messages":[]}',
        "photos/photo.jpg": b"original-photo",
    }
    _write_zip(bundle_path, original_files)
    _report_zip(report_path)

    append_report_to_archive(str(bundle_path), str(report_path))

    with zipfile.ZipFile(bundle_path) as archive:
        assert set(archive.namelist()) == {
            *original_files,
            "report/index.html",
            "report/assets/report.css",
            "report/questions/q_001.html",
        }
        assert archive.read("result.json") == original_files["result.json"].encode()
        assert archive.read("photos/photo.jpg") == b"original-photo"
        assert archive.read("report/index.html") == b"report home"


@pytest.mark.parametrize(
    ("source_files", "expected_report_path"),
    [
        ({"Chat/result.json": '{"messages":[]}'}, "Chat/report/index.html"),
        ({"Export/messages.html": "<html></html>"}, "Export/report/index.html"),
    ],
)
def test_bundle_places_report_beside_nested_export_root(
    tmp_path: Path,
    source_files: dict[str, str],
    expected_report_path: str,
) -> None:
    bundle_path = tmp_path / "nested.zip"
    report_path = tmp_path / "report.zip"
    _write_zip(bundle_path, {**source_files, "Export/photos/photo.jpg": b"photo"})
    _report_zip(report_path)

    append_report_to_archive(str(bundle_path), str(report_path))

    with zipfile.ZipFile(bundle_path) as archive:
        assert archive.read(expected_report_path) == b"report home"


def test_bundle_rejects_existing_report_directory_without_changing_it(tmp_path: Path) -> None:
    bundle_path = tmp_path / "collision.zip"
    report_path = tmp_path / "report.zip"
    _write_zip(
        bundle_path,
        {
            "Chat/result.json": '{"messages":[]}',
            "Chat/report/index.html": "old report",
        },
    )
    _report_zip(report_path)

    with pytest.raises(ReportBundleConflictError, match="already contains a report directory"):
        append_report_to_archive(str(bundle_path), str(report_path))

    with zipfile.ZipFile(bundle_path) as archive:
        assert archive.read("Chat/report/index.html") == b"old report"
        assert archive.namelist().count("Chat/report/index.html") == 1


def test_build_bundle_keeps_stored_upload_unchanged_and_cleans_report_temp(
    monkeypatch,
    tmp_path: Path,
) -> None:
    original_bytes = _zip_bytes(
        tmp_path,
        "stored-original.zip",
        {"Chat/result.json": '{"messages":[]}', "Chat/photos/photo.jpg": b"photo"},
    )
    report_bytes = _zip_bytes(
        tmp_path,
        "stored-report.zip",
        {"report/index.html": "report home"},
    )
    client = _FakeMinio({"upload": original_bytes, "report": report_bytes})
    created_paths: list[Path] = []
    real_named_temporary_file = tempfile.NamedTemporaryFile

    def named_temporary_file(**kwargs):
        temp = real_named_temporary_file(dir=tmp_path, **kwargs)
        created_paths.append(Path(temp.name))
        return temp

    monkeypatch.setattr(report_bundle.tempfile, "NamedTemporaryFile", named_temporary_file)

    bundle_path = Path(
        build_report_bundle(
            client=client,
            bucket="bucket",
            upload_object_key="upload",
            report_object_key="report",
        )
    )

    assert client.objects["upload"] == original_bytes
    assert bundle_path.exists()
    assert len(created_paths) == 2
    assert not created_paths[1].exists()
    with zipfile.ZipFile(bundle_path) as archive:
        assert archive.read("Chat/report/index.html") == b"report home"

    remove_temp_file(str(bundle_path))
    assert not bundle_path.exists()

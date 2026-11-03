"""Validate a generated evidence bundle without a running GNS3 server."""
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit
import zipfile


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.targets = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if (tag == "a" and key == "href") or (tag == "img" and key == "src"):
                self.targets.append(value)


directory = Path(sys.argv[1]).resolve()
manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
with zipfile.ZipFile(str(directory) + ".zip") as archive:
    assert archive.testzip() is None, "ZIP CRC check failed"
    assert {entry.filename for entry in archive.infolist() if not entry.is_dir()} == {entry["path"] for entry in manifest["files"]} | {"manifest.json"}
    for entry in manifest["files"]:
        archived = archive.read(entry["path"])
        local = (directory / entry["path"]).read_bytes()
        assert archived == local, f"ZIP differs from local file: {entry['path']}"
        assert len(local) == entry["bytes"]
        assert hashlib.sha256(local).hexdigest() == entry["sha256"], entry["path"]

parser = Links()
parser.feed((directory / "index.html").read_text(encoding="utf-8"))
markdown = (directory / "report.md").read_text(encoding="utf-8")
targets = parser.targets + re.findall(r"\]\(([^)]+)\)", markdown)
for target in targets:
    parts = urlsplit(target)
    assert not parts.scheme and not parts.netloc, "Unexpected external report dependency"
    if not parts.path:
        continue
    resolved = (directory / unquote(parts.path)).resolve()
    assert resolved.is_relative_to(directory), "Link escapes evidence directory"
    assert resolved.is_file(), f"Missing report target: {target}"

image = manifest.get("topology_image")
if manifest.get("project_source") == "GNS3 controller API v2 GET":
    assert "source_archive" not in manifest, "Runtime still depends on a development archive"
    snapshot = json.loads((directory / manifest["project_snapshot_record"]).read_text(encoding="utf-8"))
    assert snapshot["complete"] and snapshot["project_id"] == manifest["collection"]["project_id"]
    topology_bytes = (directory / manifest["source_topology_file"]).read_bytes()
    assert hashlib.sha256(topology_bytes).hexdigest() == snapshot["topology_sha256"] == manifest["source_topology_sha256"]
    for config in manifest["config_sources"]:
        if config["status"] == "collected":
            assert (directory / config["exported_file"]).is_file()
        else:
            assert config["reason"] and config["exported_file"] is None
if image:
    actual = hashlib.sha256((directory / image["exported_file"]).read_bytes()).hexdigest()
    assert actual == image["source_sha256"], "Provided image changed"
    if image["kind"] in {"captured_gns3_window", "gns3_native_topology_screenshot"}:
        capture = json.loads((directory / image["capture_record"]).read_text(encoding="utf-8"))
        assert capture["capture_status"] == "captured"
        assert capture["image_sha256"] == actual
        assert capture["project_name"] == manifest["project_name"]
        if image["kind"] == "gns3_native_topology_screenshot":
            native = json.loads((directory / image["native_export_record"]).read_text(encoding="utf-8"))
            assert native["status"] == "saved"
            assert capture["method"] == "Native"

print(f"PASS: ZIP CRC, {len(manifest['files'])} file hashes, local report links and image provenance")

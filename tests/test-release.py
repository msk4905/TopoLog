"""Check that public release assets contain the runnable source/demo, not inputs."""
import hashlib
from pathlib import Path, PurePosixPath
import sys
import zipfile

root = Path(sys.argv[1])
forbidden = {"origin", "work", "exports", "demo-site", ".git", ".openai", "node_modules", "__pycache__", "handoff", ".topolog-workspace.json", "plan.md", "PUBLISHING.md", "UPLOAD_GUIDE.md"}
for file in root.rglob("*"):
    assert not forbidden.intersection(file.relative_to(root).parts), file
source = root / "TopoLog-v0.1.0-source.zip"
demo = root / "TopoLog-v0.1.0-demo.zip"
with zipfile.ZipFile(source) as archive:
    assert archive.testzip() is None
    names = archive.namelist()
    assert len(names) == len(set(names))
    for name in names:
        path = PurePosixPath(name)
        assert not path.is_absolute() and ".." not in path.parts
        assert not forbidden.intersection(path.parts), name
        assert not path.name.startswith(".env"), name
    for name in ["TopoLog/TopoLog.cmd", "TopoLog/RELEASE_NOTES.md", "TopoLog/VALIDATION.md", "TopoLog/docs/index.html", "TopoLog/docs/.nojekyll", "TopoLog/.gitignore", "TopoLog/tools/TopoLogWindowCapture.cs", "TopoLog/tools/collect-project.ps1", "TopoLog/tools/gns3-api.ps1", "TopoLog/tools/project-paths.ps1"]:
        assert name in names, name
    assert archive.read("TopoLog/VERSION").decode().strip() == "0.1.0"
    assert "PUBLISHING.md" not in archive.read("TopoLog/README.md").decode("utf-8-sig"), "README links to an internal document"
    for name in names:
        if name.endswith((".ps1", ".cs", ".cjs", ".html", ".md")):
            text = archive.read(name).decode("utf-8-sig")
            assert "C:/Users/Administrator" not in text and "C:\\Users\\Administrator" not in text, name
            assert "GNS_Proof" not in text and "GnsProof" not in text, name
with zipfile.ZipFile(demo) as archive:
    assert archive.testzip() is None
    names = archive.namelist()
    assert {"index.html", "app.js", "data.js", "style.css", "assets/topology.png"}.issubset(names)
    for name in names:
        assert not forbidden.intersection(PurePosixPath(name).parts)
        assert not name.endswith((".cfg", ".raw.log", ".bin", ".ini")), name
for line in (root / "SHA256SUMS.txt").read_text().splitlines():
    digest, filename = line.split("  ", 1)
    assert hashlib.sha256((root / filename).read_bytes()).hexdigest() == digest
print(f"PASS: source/demo ZIP CRC, required files, private-folder exclusions, portable paths and asset hashes; {source.stat().st_size + demo.stat().st_size:,} bytes combined")

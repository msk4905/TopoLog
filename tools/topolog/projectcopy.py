"""Collect a project whose GNS3 ID is already taken, through a temporary copy.

GNS3 opens one project per ID. A project folder copied in Explorer keeps the
same ID even after its devices or links were changed, so GNS3 would open the
other copy (the one in its default project folder, or the first one of the
selected folder) instead. TopoLog then copies the .gns3 file and its
project-files folder to a temporary folder next to the result, gives only the
copy a new ID, collects from the copy and deletes it at the end of the run.
The user's project files are only read.
"""
import json
import os
import shutil
import uuid
from pathlib import Path

MARKER = '.topolog-temporary-copies'
SUFFIX = '_임시사본'
SPARE_BYTES = 500 * 1024 * 1024        # free space kept beyond the copy itself
NAMESPACE = uuid.UUID('7c1f2a4e-63b1-4d1b-9a55-2f0c6d8e9b10')


def derived_id(project_id, file):
    """Stable TopoLog key for a second project file with an ID already used in the same folder."""
    return str(uuid.uuid5(NAMESPACE, '{}|{}'.format(project_id, str(file).replace('\\', '/').lower())))


def api_id(entry):
    """ID GNS3 knows this project by in this run (the copy's ID when a copy was used)."""
    return (entry.get('gns3_copy') or {}).get('project_id') or entry['project_id']


def open_file(entry, root):
    """The .gns3 file GNS3 opens for this project in this run."""
    copy = (entry.get('gns3_copy') or {}).get('path')
    return Path(copy) if copy else Path(root) / entry['file']


def work_root(output):
    return Path(output).parent / (Path(output).name + SUFFIX)


def size(project_file):
    total = Path(project_file).stat().st_size
    files = Path(project_file).parent / 'project-files'
    if files.is_dir():
        for base, _, names in os.walk(files, followlinks=False):
            for name in names:
                try:
                    total += (Path(base) / name).lstat().st_size
                except OSError:
                    pass
    return total


def make(project_file, root, new_id, name):
    """Copy the project file and project-files to root/name; returns the copied .gns3 path."""
    project_file, root = Path(project_file), Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / MARKER).write_text('TopoLog temporary project copies; deleted after the run.\n', encoding='utf-8')
    need = size(project_file)
    free = shutil.disk_usage(root).free
    if free < need + SPARE_BYTES:
        raise ValueError('임시 사본을 만들 공간이 부족합니다(필요 약 {:.1f}GB, 남은 공간 {:.1f}GB).'.format(
            (need + SPARE_BYTES) / 1e9, free / 1e9))
    target = root / name
    target.mkdir()
    files = project_file.parent / 'project-files'
    if files.is_dir():
        shutil.copytree(files, target / 'project-files', symlinks=False)
    data = json.loads(project_file.read_text(encoding='utf-8-sig'))
    data['project_id'] = new_id
    copied = target / project_file.name
    copied.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding='utf-8')
    return copied


def is_inside(path, root):
    try:
        return Path(path).resolve().is_relative_to(Path(root).resolve())
    except (OSError, ValueError):
        return False


def remove(root):
    """Delete a temporary copy folder TopoLog made (it must carry the marker); returns an error text or ''."""
    root = Path(root)
    if not root.is_dir():
        return ''
    if not (root / MARKER).is_file():
        return '임시 사본 표시가 없는 폴더라 지우지 않았습니다: ' + str(root)
    errors = []
    shutil.rmtree(root, onerror=lambda function, path, info: errors.append(path))
    return ('임시 사본 일부를 지우지 못했습니다: ' + str(root)) if errors or root.exists() else ''


def leftovers(parent):
    """Temporary copy folders left by an interrupted earlier run in this output folder."""
    parent = Path(parent)
    if not parent.is_dir():
        return []
    return [p for p in parent.iterdir() if p.is_dir() and p.name.endswith(SUFFIX) and (p / MARKER).is_file()]

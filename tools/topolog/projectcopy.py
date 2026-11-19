"""Collect conflicting IDs through owned, controller-managed temporary projects.

GNS3 chooses its own supported storage path. Only the new project gets a new ID
and controller name; original files and report names are preserved. Legacy
temporary-folder helpers remain available for older collection records.
"""
import json
import os
import shutil
import uuid
from pathlib import Path
from urllib.error import HTTPError

MARKER = '.topolog-temporary-copies'
SUFFIX = '_임시사본'
SPARE_BYTES = 500 * 1024 * 1024        # free space kept beyond the copy itself
NAMESPACE = uuid.UUID('7c1f2a4e-63b1-4d1b-9a55-2f0c6d8e9b10')
PROJECT_MARKER = '.topolog-owned-project.json'


def controller_name(project_file, new_id):
    data = json.loads(Path(project_file).read_text(encoding='utf-8-sig'))
    return '{} (TopoLog {})'.format(str(data.get('name') or Path(project_file).stem)[:40], new_id)


def make_on_controller(api, project_file, new_id):
    """Use the controller's own project store, which its delete API accepts.

    An empty, newly created project is closed before its topology is populated.
    Existing projects are never repointed or overwritten. The controller-returned
    path must be accessible locally, like the normal file-based load operation.
    """
    project_file = Path(project_file)
    name = controller_name(project_file, new_id)
    created = api.call('/v2/projects', 'POST', {'project_id': new_id, 'name': name})
    if created.get('project_id') != new_id or created.get('name') != name:
        raise ValueError('Controller did not create the requested temporary project')
    target = Path(created['path'])
    filename = created.get('filename') or name + '.gns3'
    try:
        if not target.is_dir() or Path(filename).name != filename or '/' in filename or '\\' in filename:
            raise ValueError('Controller temporary project storage is not locally accessible')
        api.call('/v2/projects/' + new_id + '/close', 'POST')
        (target / PROJECT_MARKER).write_text(json.dumps({'project_id':new_id}), encoding='utf-8')
        need = size(project_file)
        free = shutil.disk_usage(target).free
        if free < need + SPARE_BYTES:
            raise ValueError('임시 사본을 만들 공간이 부족합니다(필요 약 {:.1f}GB, 남은 공간 {:.1f}GB).'.format(
                (need + SPARE_BYTES) / 1e9, free / 1e9))
        files = project_file.parent / 'project-files'
        if files.is_dir():
            shutil.copytree(files, target / 'project-files', symlinks=False, dirs_exist_ok=True)
        data = json.loads(project_file.read_text(encoding='utf-8-sig'))
        data.update(project_id=new_id, name=name)
        copied = target / filename
        copied.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding='utf-8')
        return copied
    except BaseException:
        # This exact nonce and name were created above; an empty-project rollback
        # cannot target a pre-existing project chosen by a filename or model.
        try: api.call('/v2/projects/' + new_id, 'DELETE')
        except Exception: pass
        raise


def delete_controller_copy(api, copied):
    """Delete only a controller project with our exact ownership marker and path."""
    target = Path(copied['path']).parent.resolve()
    marker = target / PROJECT_MARKER
    if not marker.is_file() or json.loads(marker.read_text(encoding='utf-8')).get('project_id') != copied['project_id']:
        raise ValueError('Temporary project ownership marker does not match')
    try:
        info = api.call('/v2/projects/' + copied['project_id'])
    except HTTPError as exc:
        if exc.code != 404: raise
        shutil.rmtree(target)
        return
    if Path(info.get('path','')).resolve() != target:
        raise ValueError('Temporary project location changed')
    api.call('/v2/projects/' + copied['project_id'], 'DELETE')


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
    # GNS3 also rejects duplicate human-readable names, even for different IDs.
    # Only the disposable copy gets a unique controller name; report entries
    # and the original project keep the user's name.
    original_name = str(data.get('name') or project_file.stem)
    data['name'] = '{} (TopoLog {})'.format(original_name[:40], new_id)
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

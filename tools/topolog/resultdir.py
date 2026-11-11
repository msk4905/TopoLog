"""Layout of one result folder.

The top of a result folder holds only what people open: the HTML report
(index.html), the Word report, the topology picture folder and the AI editing
guide. Everything else (summary.json, per-project evidence folders, Markdown
records, manifest, criteria and title choices) lives in RECORDS below it.

Evidence folders are named after the project and the device, recorded as 'folder' in summary.json. Older results have no 'folder' and keep
their GNS3 id folders; project_dir() and node_dir() return either.

Results written before v1.0.0 keep everything at the top; records() returns the
folder itself for them, so they still open, refresh and rebuild.
"""
import re
from pathlib import Path

RECORDS = '수집_기록'


def records(folder):
    """Folder holding summary.json and the per-project evidence folders."""
    folder = Path(folder)
    inner = folder / RECORDS
    return inner if inner.is_dir() else folder


def summary_file(folder):
    return records(folder) / 'summary.json'


def is_result(folder):
    return summary_file(folder).is_file()


def prefix(report):
    """Path prefix, relative to the top of the result folder, for files under the records folder."""
    name = (report or {}).get('records_dir')
    return name + '/' if name else ''


def top(folder):
    """The result folder itself when someone picked its records folder."""
    folder = Path(folder)
    if folder.name == RECORDS and (folder / 'summary.json').is_file() and not (folder / RECORDS).is_dir():
        return folder.parent
    return folder


# Names Windows refuses for a file or folder, with or without an extension.
_DEVICE = re.compile(r'(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9\u00b9\u00b2\u00b3]|LPT[1-9\u00b9\u00b2\u00b3])(?:\..*)?', re.I)
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
# Files a run writes beside the evidence folders; a folder never takes these names.
RESERVED = {'summary.json', 'criteria-used.json', 'capture', 'capture-1', 'source-project.json', 'topology.json', 'result.json',
            'auto-criteria.json', 'topology-reconstructed.svg', 'manifest.json', 'findings.md'}


def folder_name(text, used, fallback):
    """Readable, Windows-safe folder name, unique (ignoring case) within `used`."""
    name = _UNSAFE.sub('_', str(text or '')).strip()
    name = name[:60].rstrip(' .') or fallback
    if _DEVICE.fullmatch(name):
        name = '_' + name
    base, number = name, 2
    while name.lower() in used or name.lower() in RESERVED:
        name = '{}_{}'.format(base, number)
        number += 1
    used.add(name.lower())
    return name


def project_dir(project):
    """Evidence folder of one project under the records folder."""
    return str(project.get('folder') or project.get('project_id', ''))


def node_dir(node):
    """Evidence folder of one device under its project folder."""
    return str(node.get('folder') or node.get('node_id', ''))


def node_dir_of(project, node_id):
    """Folder of the device with this GNS3 id (old results: the id itself)."""
    node = next((n for n in project.get('nodes', []) if n.get('node_id') == node_id), None)
    return node_dir(node) if node else str(node_id)


def evidence_dir(folder, project_id, node_id=None):
    """Evidence folder of a project (or one of its devices) in a saved result, looked up by GNS3 id."""
    import json
    data = records(folder)
    report = json.loads((data / 'summary.json').read_text(encoding='utf-8'))
    project = next((p for p in report.get('projects', []) if p.get('project_id') == project_id), {'project_id': project_id})
    path = data / project_dir(project)
    return path / node_dir_of(project, node_id) if node_id else path

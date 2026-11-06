"""One folder per result that gathers the topology pictures of every project.

Evidence stays where it was collected (<project>/capture/gns3-topology.png and
<project>/topology-reconstructed.svg, with their capture records and hashes).
This folder holds copies under readable project names, separated by origin, so
the reports, manifest and ZIP can point people to one place.
"""
import hashlib
import re
import shutil
from pathlib import Path

FOLDER = '토폴로지_사진'
KINDS = {'native': ('GNS3_실제_사진', 'GNS3 자체 Screenshot'),
         'reconstructed': ('재구성_그림', 'TopoLog가 API 좌표·연결로 그린 재구성 그림'),
         'annotated': ('GNS3_주석_추가본', 'GNS3 사진 사본에 TopoLog가 서브넷·cost·제목을 덧붙인 그림')}
README = '안내.txt'
RESERVED = re.compile(r'(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?', re.I)


def clean(name):
    text = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]+', '_', str(name or '')).strip().rstrip(' .')
    text = text[:80].rstrip(' .') or '프로젝트'
    return '_' + text if RESERVED.fullmatch(text) else text


def _stems(report):
    """Deterministic readable names; duplicates get a short project id."""
    projects = [p for p in report.get('projects', []) if p.get('project_id')]
    counts = {}
    for p in projects:
        counts[clean(p.get('name'))] = counts.get(clean(p.get('name')), 0) + 1
    stems = {}
    for p in projects:
        stem = clean(p.get('name'))
        if counts[stem] > 1:
            stem += '-' + re.sub(r'[^0-9A-Za-z]', '', str(p['project_id']))[:8]
        stems[p['project_id']] = stem
    return stems


def planned(report):
    """{(project_id, kind): relative gallery path} for images the run produced."""
    stems, result = _stems(report), {}
    for p in report.get('projects', []):
        pid = p.get('project_id')
        for kind, (folder, _) in KINDS.items():
            item = (p.get('topology_images') or {}).get(kind) or {}
            if pid in stems and item.get('status') in ('captured', 'generated') and item.get('file'):
                suffix = Path(item['file']).suffix.lower()
                if suffix in ('.png', '.svg'):
                    result[(pid, kind)] = '{}/{}/{}{}'.format(FOLDER, folder, stems[pid], suffix)
    return result


def source(report, project, kind):
    """Original picture, relative to the top of the result folder."""
    from .resultdir import prefix
    item = (project.get('topology_images') or {}).get(kind) or {}
    return '{}{}/{}'.format(prefix(report), project.get('project_id', ''), item.get('file', '')) if item.get('file') else None


def build(report, output):
    """Create or refresh the gallery; never removes files it did not write."""
    from .security import safe_path, linked
    output = Path(output)
    plan = planned(report)
    target_root = output / FOLDER
    if linked(target_root):
        raise ValueError('Linked topology picture folder is not supported')
    written = []
    by_id = {p.get('project_id'): p for p in report.get('projects', [])}
    for (pid, kind), relative in sorted(plan.items()):
        origin = safe_path(output, source(report, by_id[pid], kind))
        if not origin.is_file():
            continue
        target = safe_path(output, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = origin.read_bytes()
        if not target.is_file() or hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(data).digest():
            shutil.copyfile(origin, target)
        written.append(relative)
    if written:
        lines = ['TopoLog 토폴로지 사진 모음', '',
                 'GNS3_실제_사진: GNS3 자체 Screenshot 기능으로 저장한 사진입니다. 사진 내용은 직접 확인하세요.',
                 '재구성_그림: GNS3 API의 장비 좌표와 연결로 TopoLog가 그린 그림이며 실제 화면이 아닙니다.',
                 'GNS3_주석_추가본: GNS3 사진의 사본에 수집한 설정의 서브넷·cost와 제목을 TopoLog가 덧붙인 그림입니다.',
                 '  원래 GNS3 사진은 GNS3_실제_사진에 그대로 있으며, 직접 글자를 넣어 둔 프로젝트는 주석을 넣지 않았습니다.',
                 '', '원본 기록은 ' + ('수집_기록 폴더 안의 ' if report.get('records_dir') else '') + '각 프로젝트 폴더(capture/, topology-reconstructed.svg)에 그대로 있습니다.',
                 '사진을 저장하지 못한 프로젝트는 이 폴더에 파일이 없고, 보고서에 사유가 적혀 있습니다.']
        (target_root / README).write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return written


def path_for(report, project, kind, output=None):
    """Relative path used by reports: gallery copy when present, else the original."""
    relative = planned(report).get((project.get('project_id'), kind))
    if relative and (output is None or (Path(output) / relative).is_file()):
        return relative
    return source(report, project, kind)

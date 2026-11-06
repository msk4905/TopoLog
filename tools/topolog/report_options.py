"""Report detail range chosen by the user and kept with each result.

'representative' writes Word/document-Markdown detail sections for at most six
projects chosen by the number of distinct detected feature kinds (not by
importance or completeness); 'all' writes them for every project. The HTML
report and the raw evidence always cover every project.
"""
import json
import os
from pathlib import Path

SCOPES = ('representative', 'all')
DEFAULT = 'representative'
FILE = 'report-options.json'
LABELS = {'representative': '대표 프로젝트만 상세 정리(최대 6개)', 'all': '전체 프로젝트 상세 정리'}


def normalize(value):
    return value if value in SCOPES else DEFAULT


def detail_scope(report, folder=None):
    """Latest choice: a regeneration choice saved next to the result wins over the run setting."""
    if folder is not None:
        try:
            data = json.loads((Path(folder) / FILE).read_text(encoding='utf-8'))
            if data.get('detail_scope') in SCOPES:
                return data['detail_scope']
        except (OSError, ValueError, AttributeError):
            pass
    return normalize((report.get('report_options') or {}).get('detail_scope'))


def save(folder, value):
    """Record a regeneration choice without rewriting summary.json evidence."""
    value = normalize(value)
    path = Path(folder) / FILE
    temporary = path.with_name(FILE + '.tmp')
    temporary.write_text(json.dumps({'schema': 'topolog-report-options-v1', 'detail_scope': value}, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)
    return value

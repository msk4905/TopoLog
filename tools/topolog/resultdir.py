"""Layout of one result folder.

The top of a result folder holds only what people open: the HTML report
(index.html), the Word report, the topology picture folder and the AI editing
guide. Everything else (summary.json, per-project evidence folders named by
GNS3 project id, Markdown records, manifest, criteria and title choices) lives
in RECORDS below it.

Results written before v1.0.0 keep everything at the top; records() returns the
folder itself for them, so they still open, refresh and rebuild.
"""
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

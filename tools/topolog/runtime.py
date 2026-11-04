"""Identify loaded code and refuse a new collection from an outdated window."""
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
def signatures():
    return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.glob('*.py')}

LOADED = signatures()
VERSION = (ROOT.parent.parent/'VERSION').read_text(encoding='utf-8').strip()


def ensure_current():
    if signatures()!=LOADED or (ROOT.parent.parent/'VERSION').read_text(encoding='utf-8').strip()!=VERSION:
        raise ValueError('이 창을 연 뒤 TopoLog 코드가 업데이트됐습니다. 현재 TopoLog 창을 닫고 CMD를 다시 실행하세요. GNS3는 종료할 필요 없습니다.')


def metadata():
    return {'version':VERSION,'python_executable':sys.executable,'source_directory':str(ROOT),
            'source_hashes':LOADED,'html_format':'structured-html-v1'}

"""Repair a local GNS3 helper setting without changing project files."""
import configparser
import os
from pathlib import Path
import re
import tempfile
import uuid
from . import backend


class RestartRequired(ValueError):
    pass


def _config_path(config_path=None):
    return Path(config_path) if config_path else Path(os.environ.get('APPDATA', Path.home() / '.config')) / 'GNS3/2.2/gns3_server.ini'


def _repair_helper(executable, config_path, section, key, relatives, label):
    path = _config_path(config_path)
    # Only an existing server configuration is edited; no guessed profile is created.
    original = path.read_bytes()
    text = original.decode('utf-8-sig')
    config = configparser.ConfigParser(interpolation=None)
    config.read_string(text)
    current = config.get(section, key, fallback='').strip()
    if current:
        expanded = os.path.expandvars(os.path.expanduser(current))
        if Path(expanded).is_absolute() and Path(expanded).is_file():
            return None
    root = Path(executable).resolve().parent
    helper = next((p for p in (root / relative for relative in relatives) if p.is_file()), None)
    if helper is None:
        raise ValueError('Installed {} executable not found beside the local GNS3 server.'.format(label))
    newline = '\r\n' if '\r\n' in text else '\n'
    replacement = key + ' = ' + str(helper) + newline
    lines = text.splitlines(keepends=True)
    header = re.compile(r'^\s*\[' + re.escape(section) + r'\]\s*$')
    start = next((i for i, line in enumerate(lines) if header.match(line)), None)
    if start is None:
        updated = text + (newline if text and not text.endswith(('\r', '\n')) else '') + '[' + section + ']' + newline + replacement
    else:
        end = next((i for i in range(start + 1, len(lines)) if re.match(r'^\s*\[', lines[i])), len(lines))
        keys = [i for i in range(start + 1, end) if re.match(r'^\s*' + re.escape(key) + r'\s*[:=]', lines[i], re.I)]
        if keys:
            lines[keys[0]] = replacement
        else:
            if end and not lines[end - 1].endswith(('\r', '\n')):
                lines[end - 1] += newline
            lines.insert(end, replacement)
        updated = ''.join(lines)
    bom = b'\xef\xbb\xbf' if original.startswith(b'\xef\xbb\xbf') else b''
    payload = bom + updated.encode('utf-8')
    check = configparser.ConfigParser(interpolation=None)
    check.read_string(updated)
    assert check.get(section, key) == str(helper)
    backup = path.with_name(path.name + '.topolog-' + uuid.uuid4().hex[:12] + '.bak')
    # Backup beside the private config, outside exported reports/source packages.
    with backup.open('xb') as stream:
        stream.write(original)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.topolog-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        if path.read_bytes() != original:
            raise ValueError('GNS3 configuration changed during repair; retry after closing Preferences.')
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return {'configuration': str(path), 'backup': str(backup), key: str(helper)}


def repair_dynamips(executable, config_path=None):
    return _repair_helper(executable, config_path, 'Dynamips', 'dynamips_path',
                          ('dynamips/dynamips.exe', 'dynamips.exe'), 'Dynamips')


def repair_vpcs(executable, config_path=None):
    # gns3server 2.2 VPCSVM._vpcs_path(): an unset or relative vpcs_path is only
    # resolved through PATH, which a server not started by the GNS3 GUI lacks.
    return _repair_helper(executable, config_path, 'VPCS', 'vpcs_path',
                          ('vpcs/vpcs.exe', 'vpcs.exe'), 'VPCS')


def _absolute_image(value):
    return isinstance(value, str) and bool(re.match(r'^(?:[A-Za-z]:[\\/]|[\\/])', value))


def _image_directories(config_path=None):
    config = configparser.ConfigParser(interpolation=None)
    try:
        config.read(_config_path(config_path), encoding='utf-8-sig')
    except (OSError, configparser.Error, UnicodeError):
        pass
    images = config.get('Server', 'images_path', fallback='') or str(Path.home() / 'GNS3' / 'images')
    folders = [images] + [p for p in config.get('Server', 'additional_images_path', fallback='').split(';') if p.strip()]
    return [os.path.normcase(os.path.normpath(os.path.expanduser(p.strip()))) for p in folders]


def _outside(path, folders):
    target = os.path.normcase(os.path.normpath(path))
    return not any(target == folder or target.startswith(folder.rstrip('\\/') + os.sep) for folder in folders)


def prepare(api, root, entries, executable=None, progress=None, config_path=None):
    """Edit only the configured local controller when local helpers are needed."""
    import json
    from .transport import API
    needs, absolute_images = set(), []
    for entry in entries:
        if entry.get('status') != 'pending':
            continue
        data = json.loads((Path(root) / entry['file']).read_text(encoding='utf-8-sig'))
        for node in data.get('topology', {}).get('nodes', []):
            if (node.get('compute_id') or 'local') != 'local':
                continue
            if node.get('node_type') == 'dynamips':
                needs.add('dynamips')
                image = (node.get('properties') or {}).get('image')
                if _absolute_image(image):
                    absolute_images.append((entry.get('name', entry['file']), node.get('name', ''), image))
            elif node.get('node_type') == 'vpcs':
                needs.add('vpcs')
    if not needs or api.base != API().base:
        return
    addresses, candidates = backend.discover()
    if api.host not in addresses:
        return
    computes = api.call('/v2/computes')
    local = next((c for c in computes if c.get('compute_id') == 'local'), None)
    if not local or local.get('host') not in addresses:
        return
    selected = Path(executable) if executable else next(iter(candidates), None)
    if selected is None:
        raise ValueError('Cannot locate the local GNS3 installation to repair the Dynamips path.')
    repaired = []
    try:
        if 'dynamips' in needs:
            result = repair_dynamips(selected, config_path)
            if result:
                repaired.append(('Dynamips', result['dynamips_path'], result['backup']))
        if 'vpcs' in needs:
            try:
                result = repair_vpcs(selected, config_path)
            except ValueError as exc:
                # VPCS nodes then fail individually; Dynamips/VM projects continue.
                result = None
                if progress:
                    progress('VPCS 경로를 자동 복구하지 못했습니다: ' + str(exc))
            if result:
                repaired.append(('VPCS', result['vpcs_path'], result['backup']))
    except (OSError, configparser.Error, UnicodeError) as exc:
        raise ValueError('GNS3 경로 설정을 자동 복구하지 못했습니다. 설정 파일의 접근 권한과 형식을 확인하세요.') from exc
    blocked = []
    if absolute_images:
        # gns3server 2.2 BaseManager.get_abs_image_path(): without --local an absolute
        # image outside images_path is refused with "is not allowed on this remote server".
        version = api.call('/v2/version')
        if isinstance(version, dict) and version.get('local') is False:
            folders = _image_directories(config_path)
            blocked = [item for item in absolute_images if _outside(item[2], folders)]
    if not repaired and not blocked:
        return
    lines = []
    for label, helper, backup in repaired:
        lines.append('{} 경로를 자동 복구했습니다: {}'.format(label, helper))
    if blocked:
        sample = blocked[0]
        lines.append('현재 Desktop GNS3 서버가 local 모드가 아닙니다(/v2/version local=false). '
                     '이 상태에서는 images 폴더 밖 절대 경로 IOS 이미지를 쓰는 프로젝트를 열 수 없습니다 '
                     '({}개 노드, 예: {} / {} / {}). GNS3 GUI가 직접 띄운 서버는 --local로 실행되지만, '
                     '다른 방식으로 먼저 실행된 gns3server.exe는 GUI가 종료·교체하지 않습니다.'.format(len(blocked), *sample))
    lines.append('GNS3를 완전히 종료하고 작업 관리자에서 gns3server.exe가 남아 있지 않은지 확인한 뒤 GNS3를 다시 켜 주세요. '
                 'GNS3 VM 연결이 준비되면 TopoLog에서 다시 수집하세요. 프로젝트 파일과 수동 경로 편집은 필요 없습니다.')
    for label, helper, backup in repaired:
        lines.append('원본 설정 백업({}): {}'.format(label, backup))
    message = '\n'.join(lines)
    if progress:
        progress(message)
    raise RestartRequired(message)

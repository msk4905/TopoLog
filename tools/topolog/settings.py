"""User-owned paths; never consult development reference folders."""
import configparser
import json
import os
from pathlib import Path
import tempfile


def settings_path():
    return Path(os.environ.get('LOCALAPPDATA', Path.home() / '.config')) / 'TopoLog' / 'settings.json'


def project_default(appdata=None, home=None):
    home = Path(home or Path.home())
    config_root = Path(appdata or os.environ.get('APPDATA', home / '.config')) / 'GNS3' / '2.2'
    keys = {'projects_path', 'projects_directory', 'project_directory', 'projects_dir', 'project_dir'}
    def find(value):
        if not isinstance(value, dict):
            return None
        for key, item in value.items():
            if key in keys and isinstance(item, str) and item.strip():
                return Path(os.path.expandvars(item)).expanduser()
        for item in value.values():
            result = find(item)
            if result:
                return result
    for name in ('gns3_gui.ini', 'gns3_controller.ini', 'gns3_server.ini'):
        path = config_root / name
        try:
            text = path.read_text(encoding='utf-8-sig')
            if text.lstrip().startswith('{'):
                candidate = find(json.loads(text))
                if candidate:
                    return candidate
            else:
                config = configparser.ConfigParser(interpolation=None)
                config.read_string(text)
                for section in config.sections():
                    for key in keys:
                        if config.has_option(section, key):
                            return Path(os.path.expandvars(config.get(section, key))).expanduser()
        except (OSError, ValueError, configparser.Error):
            continue
    return home / 'GNS3' / 'projects'


def duplicate_projects(root, entries, default=None):
    """Projects of the selected folder whose ID also exists in GNS3's default project folder.

    GNS3 registers every project of its default folder at start and opens one project per ID,
    so such a project would be opened from the default folder instead of the selected one.
    Read-only: only .gns3 files one level below the default folder are read.
    """
    default = Path(default or project_default())
    try:
        if os.path.normcase(str(Path(root).resolve())) == os.path.normcase(str(default.resolve())) or not default.is_dir():
            return []
    except OSError:
        return []
    registered = {}
    for file in list(default.glob('*.gns3')) + list(default.glob('*/*.gns3')):
        try:
            if file.stat().st_size > 20_000_000:
                continue
            pid = json.loads(file.read_text(encoding='utf-8-sig')).get('project_id')
        except (OSError, ValueError, AttributeError):
            continue
        if pid:
            registered.setdefault(pid, file)
    found = []
    for entry in entries:
        other = registered.get(entry.get('project_id'))
        if entry.get('status') == 'pending' and other is not None:
            found.append({'file': entry.get('file'), 'project_id': entry['project_id'], 'gns3_copy': str(other)})
    return found


def load(path=None):
    """Saved folders and options. GNS3/VM auto start is always on: an older saved
    'auto_start_gns3': false is ignored so it cannot silently block the run.
    Passwords are never part of the settings file."""
    file = Path(path or settings_path())
    result = {'project_directory': str(project_default()),
              'output_directory': str(Path.home() / 'Documents' / 'TopoLog' / 'exports'),
              'auto_start_gns3': True, 'capture_screenshot': True, 'allow_enable': True, 'annotate': True,
              'detail_scope': 'representative'}
    if file.exists():
        try:
            data = json.loads(file.read_text(encoding='utf-8-sig'))
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        for key in ('project_directory', 'output_directory'):
            if key in data and isinstance(data[key], str) and data[key].strip():
                result[key] = data[key]
        for flag in ('capture_screenshot', 'allow_enable', 'annotate'):
            if isinstance(data.get(flag), bool):
                result[flag] = data[flag]
        if data.get('detail_scope') in ('representative', 'all'):
            result['detail_scope'] = data['detail_scope']
        if data.get('auto_start_gns3') is False:
            result['migrated_auto_start'] = True
    return result


def validate(projects, output):
    projects = Path(projects).expanduser().resolve(strict=True)
    output = Path(output).expanduser().resolve()
    if not projects.is_dir():
        raise ValueError('Project folder must be a directory')
    if output == projects or output.is_relative_to(projects):
        raise ValueError('Choose an output folder outside the project folder')
    if output.exists() and not output.is_dir():
        raise ValueError('Output folder is a file')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=output):
        pass
    return projects, output


def save(projects, output, path=None, auto_start_gns3=None, capture_screenshot=None, allow_enable=None, detail_scope=None,
         annotate=None):
    """Store folders and on/off choices only (never a password).
    auto_start_gns3 is accepted for older callers and not stored: it is always on."""
    projects, output = validate(projects, output)
    path = Path(path or settings_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    values = {'project_directory': str(projects), 'output_directory': str(output)}
    try:
        previous = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        previous = {}
    if not isinstance(previous, dict):
        previous = {}
    for flag, value in (('capture_screenshot', capture_screenshot), ('allow_enable', allow_enable), ('annotate', annotate)):
        if value is None:
            value = previous.get(flag)
        if isinstance(value, bool):
            values[flag] = value
    scope = detail_scope if detail_scope is not None else previous.get('detail_scope')
    if scope in ('representative', 'all'):
        values['detail_scope'] = scope
    data = json.dumps(values, indent=2)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as temp:
        temp.write(data)
        name = temp.name
    os.replace(name, path)

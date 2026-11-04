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


def load(path=None):
    file = Path(path or settings_path())
    result = {'project_directory': str(project_default()),
              'output_directory': str(Path.home() / 'Documents' / 'TopoLog' / 'exports'),
              'auto_start_gns3': True, 'capture_screenshot': True}
    if file.exists():
        data = json.loads(file.read_text(encoding='utf-8-sig'))
        for key in ('project_directory', 'output_directory'):
            if key in data and isinstance(data[key], str) and data[key].strip():
                result[key] = data[key]
        for flag in ('auto_start_gns3', 'capture_screenshot'):
            if isinstance(data.get(flag), bool):
                result[flag] = data[flag]
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


def save(projects, output, path=None, auto_start_gns3=None, capture_screenshot=None):
    projects, output = validate(projects, output)
    path = Path(path or settings_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    values = {'project_directory': str(projects), 'output_directory': str(output)}
    try:
        previous = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        previous = {}
    for flag, value in (('auto_start_gns3', auto_start_gns3), ('capture_screenshot', capture_screenshot)):
        if value is None and isinstance(previous, dict):
            value = previous.get(flag)
        if isinstance(value, bool):
            values[flag] = value
    data = json.dumps(values, indent=2)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as temp:
        temp.write(data)
        name = temp.name
    os.replace(name, path)

"""Editable explicit expectations and offline assessment of retained evidence."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from . import profiles
from .engine import draft, validate_rules, load_rules, now, save, verdict, export, guid, describe_switch, describe_builtin, BUILTIN_TYPES


def make_rule(kind, fields):
    if kind == 'ospf_neighbors':
        rule = {'kind': kind, 'neighbors': [x.strip() for x in fields['neighbors'].replace('\n', ',').split(',') if x.strip()]}
    elif kind == 'route':
        rule = {'kind': kind, 'prefix': fields['prefix'].strip(), 'protocol': fields['protocol'].strip()}
        if fields.get('next_hop', '').strip():
            rule['next_hop'] = fields['next_hop'].strip()
    elif kind == 'ping':
        rule = {'kind': kind, 'target': fields['target'].strip(), 'min_percent': float(fields.get('min_percent') or 100)}
    elif kind == 'contains':
        rule = {'kind': kind, 'command': fields['command'].strip(), 'expected': fields['expected'].splitlines()}
    else:
        raise ValueError('Unsupported editor criterion')
    # An explicit driver is checked again when the node is saved.
    if not any(_valid(rule, driver) for driver in ('ios', 'asa', 'vpcs')):
        raise ValueError('기대값 형식이나 허용 명령을 확인하세요.')
    return rule


def _valid(rule, driver):
    try:
        profiles.command_for(rule, driver)
        return True
    except (ValueError, TypeError, KeyError):
        return False


class CriteriaDocument:
    def __init__(self, root, path=None):
        self.data = draft(root)
        if path:
            for pid, saved in load_rules(path).items():
                if pid not in self.data['projects']:
                    self.data['projects'][pid] = saved
                    continue
                project = self.data['projects'][pid]
                project['profiles'] = saved.get('profiles', [])
                for nid, node in saved.get('nodes', {}).items():
                    # Preserve missing targets so they remain visible to validation.
                    project['nodes'][nid] = {**project['nodes'].get(nid, {}), **node}

    def node(self, pid, nid):
        return self.data['projects'][pid]['nodes'][nid]

    def update(self, pid, nid, platform, selected, checks):
        candidate = copy.deepcopy(self.data)
        candidate['projects'][pid]['nodes'][nid].update(platform=platform, profiles=selected, checks=copy.deepcopy(checks))
        validate_rules(candidate)
        self.data = candidate

    def write(self, path):
        validate_rules(self.data)
        path = Path(path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(self.data, stream, ensure_ascii=False, indent=2)
            os.replace(temporary, path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()


def reassess(source, output, criteria_path=None):
    """No API or console access; original run stays unchanged."""
    from .resultdir import RECORDS, records, top
    source, output = top(Path(source).resolve(strict=True)), Path(output).resolve()
    if output == source or output.is_relative_to(source):
        raise ValueError('재판정 결과는 원본 결과 폴더 밖에 저장하세요.')
    if output.exists() or Path(str(output) + '.zip').exists():
        raise FileExistsError('Assessment output exists')
    rules = load_rules(criteria_path)
    from .security import ensure_tree,validate_report_paths
    ensure_tree(source)
    source_data = records(source)
    original = (source_data / 'summary.json').read_bytes()
    report = json.loads(original)
    if report.get('schema') != 'topolog-batch-v2':
        raise ValueError('TopoLog 일괄 결과 폴더를 선택하세요.')
    validate_report_paths(report,source)
    for project in report['projects']:
        if project.get('project_id'):
            guid(project['project_id'])
        for node in project.get('nodes', []):
            guid(node['node_id'])
    # Do not follow externally linked evidence into a new export.
    if any(p.is_symlink() for p in source.rglob('*')):
        raise ValueError('Symbolic links in evidence are not supported')
    from .naming import is_generated_report
    from .gallery import FOLDER
    from .report_options import FILE as OPTIONS
    # Evidence and title choices are copied into the new records folder; reports and pictures are made again.
    generated = {p.name for p in source_data.iterdir() if is_generated_report(p.name)} | {
        'summary.json', 'index.html', 'report.md', 'ai_prompt.md', 'manifest.json', 'findings.md', 'criteria-used.json',
        FOLDER, OPTIONS, RECORDS, 'report-document-error.txt'}
    output.mkdir(parents=True)
    data = output / RECORDS
    shutil.copytree(source_data, data, ignore=lambda folder, names: list(generated & set(names)) if Path(folder) == source_data else [])
    report['records_dir'] = RECORDS
    report['assessment'] = {'assessed_at': now(), 'source_directory': str(source),
                            'source_summary_sha256': hashlib.sha256(original).hexdigest()}
    save(data / 'criteria-used.json', json.loads(Path(criteria_path).read_text(encoding='utf-8-sig')) if criteria_path else {'schema':'topolog-rules-v1','projects':{}})
    report['unmatched_rule_projects'] = sorted(set(rules) - {p.get('project_id') for p in report['projects']})
    for project in report['projects']:
        pid = project.get('project_id')
        from .resultdir import project_dir, node_dir
        spec = rules.get(pid, {})
        expected = spec.get('nodes', {})
        project['missing_rule_nodes'] = sorted(set(expected) - {n['node_id'] for n in project.get('nodes', [])})
        saved_topology = source_data / project_dir(project) / 'topology.json'
        saved_nodes = {}
        if saved_topology.is_file():
            try:
                saved_nodes = {n.get('node_id'): n for n in json.loads(saved_topology.read_text(encoding='utf-8')).get('nodes', [])}
            except (OSError, ValueError, AttributeError):
                saved_nodes = {}
        if saved_nodes and not project.get('topology_ports'):
            project['topology_ports'] = {nid: [{k: p.get(k) for k in ('adapter_number', 'port_number', 'name')} for p in n.get('ports', []) or []]
                                         for nid, n in saved_nodes.items()}
        for node in project.get('nodes', []):
            if node.get('node_type') in BUILTIN_TYPES:
                # Re-derived only from the settings saved at collection time.
                node.update(describe_builtin(saved_nodes.get(node['node_id'], node)))
                if node.get('switch_ports') is not None:
                    save(data / project_dir(project) / node_dir(node) / 'switch-ports.json', {'source': 'saved topology.json node properties.ports_mapping', 'ports': node['switch_ports']})
                save(data / project_dir(project) / node_dir(node) / 'collection.json', node)
                continue
            node_spec = expected.get(node['node_id'], {})
            checks = node_spec.get('checks', [])
            mismatch = node_spec.get('platform', 'auto') not in ('auto', node.get('platform'))
            node['checks'] = [{'criterion': check, **({'status': 'inconclusive', 'reason': 'Driver differs from recorded OS'}
                             if mismatch else profiles.evaluate(check, node.get('commands', []), node.get('platform', 'unknown')))}
                              for check in checks]
            if not checks:
                node['checks'] = [{'status': 'unverified', 'reason': 'No explicit expected behavior configured'}]
            save(data / project_dir(project) / node_dir(node) / 'collection.json', node)
        topology_path = source_data / project_dir(project) / 'topology.json'
        if not project.get('topology_links') and topology_path.is_file():
            project['topology_links'] = json.loads(topology_path.read_text(encoding='utf-8'))['links']
        if topology_path.is_file():
            from .topology import reconstruct
            reconstruct(json.loads(topology_path.read_text(encoding='utf-8')),data/project_dir(project)/'topology-reconstructed.svg')
            images=project.setdefault('topology_images',{})
            images['reconstructed']={'status':'generated','file':'topology-reconstructed.svg','kind':'API 좌표·연결 기반 재구성'}
            images.setdefault('native',{'status':'unavailable','reason':'기존 결과에 실제 GNS3 사진이 없습니다. 새 수집이 필요합니다.'})
        from . import features
        features.apply(project, expected)
        for node in project.get('nodes', []):
            if node.get('node_type') in BUILTIN_TYPES:continue
            save(data / project_dir(project) / node_dir(node) / 'collection.json', node)
        if pid and (data / project_dir(project)).is_dir():
            save(data / project_dir(project) / 'auto-criteria.json', {'schema':'topolog-auto-v1', 'nodes':
                 {n['node_id']:n.get('automatic_checks',[]) for n in project.get('nodes',[]) if n.get('node_type') not in BUILTIN_TYPES}})
        project['verdict'] = verdict(project.get('nodes', []))
        if project['missing_rule_nodes'] and project['verdict'] != 'failed':
            project['verdict'] = 'inconclusive'
        if pid and (data / project_dir(project)).is_dir():
            save(data / project_dir(project) / 'result.json', project)
    report['verdict'] = 'inconclusive'
    if any(p.get('verdict') == 'failed' for p in report['projects']):
        report['verdict'] = 'failed'
    elif (report['projects'] and not report.get('cancelled') and not report['unmatched_rule_projects']
          and all(p['status'] == 'completed' and p['verdict'] == 'passed' for p in report['projects'])):
        report['verdict'] = 'passed'
    save(data / 'summary.json', report)
    export(output, report)
    return report

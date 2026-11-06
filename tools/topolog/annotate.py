"""Subnet, cost and title captions added to a copy of the GNS3 screenshot.

The GNS3 screenshot itself is evidence and is never changed. TopoLog writes a
separate picture (<project>/capture/gns3-topology-annotated.png) next to it and a
record (capture/annotation.json) that says how it was made:

- positions come from the project coordinates, matched to the screenshot by
  locating the node icons in the picture (mode "on_image");
- when that match is not certain, the captions go into a strip under the picture
  instead of onto it (mode "legend"), so nothing is drawn in the wrong place;
- projects that already carry text written by the user in GNS3 are skipped;
- values come from the collected running-config only (interface address and
  "ip ospf cost"); nothing is guessed or evaluated.

Drawing is done by tools/annotate-topology.ps1 with the Windows fonts named in
the project (GNS3 labels: TypeWriter 10 pt bold, shown by Qt with Tahoma).
"""
import html
import ipaddress
import json
import os
import re
import struct
import subprocess
import zlib
from pathlib import Path

FILE = 'capture/gns3-topology-annotated.png'
RECORD = 'capture/annotation.json'
TITLES = 'annotation-titles.json'
CHOICES = ('candidate', 'project')
# Router names and GNS3 port labels are drawn by GNS3 itself; TopoLog only adds what GNS3 cannot show.
CAPTION_KINDS = ('subnet', 'cost')
LABEL_FONT = {'family': 'TypeWriter', 'size': 10.0, 'bold': True, 'color': '#000000'}
TITLE_FONT = {'family': '맑은 고딕', 'size': 20.0, 'bold': True, 'color': '#000000'}


# ------------------------------------------------------------------ picture

def read_png(data):
    """(width, height, rgb rows) for 8-bit non-interlaced PNG; stdlib only."""
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('Not a PNG file')
    pos, chunks, idat = 8, {}, []
    while pos + 8 <= len(data):
        length, kind = struct.unpack('>I4s', data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b'IDAT':
            idat.append(body)
        elif kind == b'IEND':
            break
        else:
            chunks.setdefault(kind, body)
    width, height, depth, color, _, _, interlace = struct.unpack('>IIBBBBB', chunks[b'IHDR'])
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color)
    if depth != 8 or interlace or channels is None or width * height > 40_000_000:
        raise ValueError('Unsupported PNG layout')
    raw = zlib.decompress(b''.join(idat))
    stride = width * channels
    rows, previous = [], bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        kind, line = raw[start], bytearray(raw[start + 1:start + 1 + stride])
        if kind == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 255
        elif kind == 2:
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 255
        elif kind == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 255
        elif kind == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b, c = previous[i], previous[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        elif kind != 0:
            raise ValueError('Bad PNG filter')
        rows.append(bytes(line))
        previous = line
    palette = chunks.get(b'PLTE', b'')

    def rgb(row):
        if color == 2:
            return row
        if color == 6:
            out = bytearray()
            for i in range(0, len(row), 4):
                alpha = row[i + 3]
                out += bytes(255 - ((255 - v) * alpha // 255) for v in row[i:i + 3])
            return bytes(out)
        if color == 3:
            return b''.join(palette[v * 3:v * 3 + 3] or b'\xff\xff\xff' for v in row)
        step = channels
        return b''.join(bytes((row[i],) * 3) for i in range(0, len(row), step))
    return width, height, [rgb(r) for r in rows]


class Ink:
    """Summed-area table of non-background pixels for O(1) box queries."""

    def __init__(self, width, height, rows, threshold=200):
        self.width, self.height = width, height
        table = [[0] * (width + 1) for _ in range(height + 1)]
        for y, row in enumerate(rows):
            running, above, current = 0, table[y], table[y + 1]
            for x in range(width):
                i = x * 3
                if min(row[i], row[i + 1], row[i + 2]) < threshold:
                    running += 1
                current[x + 1] = above[x + 1] + running
        self.table = table

    def sum(self, x, y, w, h):
        x0, y0 = max(0, int(round(x))), max(0, int(round(y)))
        x1, y1 = min(self.width, int(round(x + w))), min(self.height, int(round(y + h)))
        if x0 >= x1 or y0 >= y1:
            return 0
        t = self.table
        return t[y1][x1] - t[y0][x1] - t[y1][x0] + t[y0][x0]

    def any(self, x, y, radius=2):
        x0, y0 = max(0, int(x) - radius), max(0, int(y) - radius)
        x1, y1 = min(self.width, int(x) + radius + 1), min(self.height, int(y) + radius + 1)
        if x0 >= x1 or y0 >= y1:
            return False
        t = self.table
        return t[y1][x1] - t[y0][x1] - t[y1][x0] + t[y0][x0] > 0


def _box(node):
    return float(node['x']), float(node['y']), float(node.get('width') or 60), float(node.get('height') or 45)


def calibrate(nodes, ink):
    """Offset from scene to picture coordinates, or None when it is not certain.

    The GNS3 screenshot renders the scene at 1:1, so only a translation is
    searched. Each node box should hold its icon: the score is the ink inside
    every node box. The match counts only when every box is well inked and no
    clearly different offset scores nearly as well.
    """
    placed = [n for n in nodes if n.get('x') is not None and n.get('y') is not None]
    if not placed:
        return None
    boxes = [_box(n) for n in placed]
    min_x, min_y = min(b[0] for b in boxes), min(b[1] for b in boxes)
    max_x, max_y = max(b[0] + b[2] for b in boxes), max(b[1] + b[3] for b in boxes)
    span_x = range(int(-min_x), int(ink.width - max_x) + 1)
    span_y = range(int(-min_y), int(ink.height - max_y) + 1)
    if not span_x or not span_y:
        return None

    ring = 4

    def score(ox, oy):
        total = 0
        for x, y, w, h in boxes:
            inside = ink.sum(x + ox, y + oy, w, h)
            around = ink.sum(x + ox - ring, y + oy - ring, w + 2 * ring, h + 2 * ring) - inside
            total += inside - around
        return total
    coarse = sorted(((score(ox, oy), ox, oy) for ox in span_x[::2] for oy in span_y[::2]), reverse=True)
    best = None
    for _, cx, cy in coarse[:8]:
        for ox in range(cx - 2, cx + 3):
            for oy in range(cy - 2, cy + 3):
                if ox in span_x and oy in span_y:
                    s = score(ox, oy)
                    if best is None or s > best[0]:
                        best = (s, ox, oy)
    s, ox, oy = best
    fill = min(ink.sum(x + ox, y + oy, w, h) / max(1, w * h) for x, y, w, h in boxes)
    rival = max((c[0] for c in coarse if abs(c[1] - ox) > 6 or abs(c[2] - oy) > 6), default=0)
    sharp = max(score(ox + dx, oy + dy) for dx, dy in ((4, 0), (-4, 0), (0, 4), (0, -4)))
    certain = s > 0 and fill >= .2 and rival <= .9 * s and sharp <= .96 * s
    return {'offset': [ox, oy], 'score': s, 'rival': rival, 'neighbour': sharp, 'min_fill': round(fill, 3), 'certain': certain}


# ------------------------------------------------------------------ values

def _running_config(node):
    items = [c for c in node.get('commands', []) if c.get('command') == 'show running-config'
             and c.get('status') == 'collected' and not c.get('cli_error') and c.get('text')]
    return items[-1]['text'] if items else ''


def interfaces(config):
    """{interface name: {'ip': IPv4Interface, 'cost': int}} from a running-config."""
    result, current = {}, None
    for raw in (config or '').splitlines():
        m = re.match(r'interface (\S+)', raw)
        if m:
            current = result.setdefault(m[1], {})
            continue
        if raw[:1] not in (' ', '\t'):
            current = None
            continue
        if current is None:
            continue
        m = re.match(r'\s+ip address (\d+\.\d+\.\d+\.\d+) (\d+\.\d+\.\d+\.\d+)\s*$', raw)
        if m and 'ip' not in current:
            try:
                current['ip'] = ipaddress.IPv4Interface(m[1] + '/' + m[2])
            except ValueError:
                pass
        m = re.match(r'\s+ip ospf cost (\d+)', raw) or re.match(r'\s+ospf cost (\d+)', raw)
        if m:
            current['cost'] = int(m[1])
    return result


def _port_name(topology, ports, end):
    for port in ports.get(end.get('node_id')) or []:
        if port.get('adapter_number') == end.get('adapter_number') and port.get('port_number') == end.get('port_number'):
            return port.get('name')
    node = next((n for n in topology.get('nodes', []) if n.get('node_id') == end.get('node_id')), {})
    for port in node.get('ports', []) or []:
        if port.get('adapter_number') == end.get('adapter_number') and port.get('port_number') == end.get('port_number'):
            return port.get('name')
    return None


def shown(titles, project):
    """Caption kinds chosen for this project in the title screen; all kinds when nothing was chosen."""
    saved = ((titles or {}).get(str(project.get('project_id'))) or {}).get('show')
    if not isinstance(saved, dict):
        return {kind: True for kind in CAPTION_KINDS}
    return {kind: bool(saved.get(kind, True)) for kind in CAPTION_KINDS}


def link_values(project, topology, configs=None, show=None):
    """Per link: both ends, subnet and cost text taken from the collected configs.

    'all_lines' always holds every value found; 'lines' only the kinds in show."""
    show = show or {kind: True for kind in CAPTION_KINDS}
    nodes = {n.get('node_id'): n for n in topology.get('nodes', [])}
    if configs is None:
        configs = {n.get('node_id'): _running_config(n) for n in project.get('nodes', [])}
    parsed = {nid: interfaces(text) for nid, text in configs.items()}
    ports = project.get('topology_ports') or {}
    result = []
    for link in topology.get('links', []):
        ends = link.get('nodes', [])
        if len(ends) != 2 or not all(e.get('node_id') in nodes for e in ends):
            continue
        info = [parsed.get(e['node_id'], {}).get(_port_name(topology, ports, e) or '', {}) for e in ends]
        networks = [str(i['ip'].network) for i in info if 'ip' in i]
        costs = [i['cost'] for i in info if 'cost' in i]
        subnet = networks[0] if networks else None
        cost = ('cost : ' + (str(costs[0]) if len(set(costs)) == 1 else ' / '.join(str(c) for c in costs))) if costs else None
        lines = [v for kind, v in (('subnet', subnet), ('cost', cost)) if v and show.get(kind)]
        result.append({'all_lines': [v for v in (subnet, cost) if v], 'ends': [nodes[e['node_id']] for e in ends], 'labels': [e.get('label') or {} for e in ends],
                       'lines': lines, 'names': [nodes[e['node_id']].get('name', '') for e in ends],
                       'ports': [_port_name(topology, ports, e) or '' for e in ends]})
    return result


def user_text(topology):
    """Text the user already wrote on the GNS3 canvas (titles, subnets, notes)."""
    found = []
    for drawing in topology.get('drawings', []) or []:
        for text in re.findall(r'<text[^>]*>(.*?)</text>', drawing.get('svg') or '', re.S):
            text = html.unescape(re.sub(r'<[^>]+>', '', text)).strip()
            if text:
                found.append(text)
    return found


# ------------------------------------------------------------------ titles

def candidate(project, topology, links=None):
    """Plain description of what was collected; no evaluation words."""
    from .portfolio import _topic
    text = _topic({**project, 'topology_images': {}}, {**topology, 'drawings': []})
    costs = sorted({int(n) for item in (links or []) for line in item.get('all_lines', item['lines']) if line.startswith('cost : ')
                    for n in re.findall(r'\d+', line)})
    if costs:
        text += ' · 경로 비용(cost {})'.format('/'.join(str(c) for c in costs))
    return text


def load_titles(folder):
    try:
        from .resultdir import records
        data = json.loads((records(folder) / TITLES).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    projects = data.get('projects') if isinstance(data, dict) else None
    return {str(k): v for k, v in (projects or {}).items() if isinstance(v, dict)}


def save_titles(folder, titles):
    clean = {}
    for pid, item in titles.items():
        choice = item.get('choice') if item.get('choice') in CHOICES else 'candidate'
        text = re.sub(r'[\x00-\x1f\x7f]', ' ', str(item.get('text', ''))).strip()[:120]
        clean[str(pid)] = {'choice': choice, 'text': text}
        if isinstance(item.get('show'), dict):
            clean[str(pid)]['show'] = {kind: bool(item['show'].get(kind, True)) for kind in CAPTION_KINDS}
    from .resultdir import records
    target = records(folder) / TITLES
    temporary = target.with_name(target.name + '.tmp')
    temporary.write_text(json.dumps({'schema': 'topolog-annotation-titles-v1', 'projects': clean},
                                    ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, target)
    return clean


def resolve_title(project, topology, titles, links=None):
    """(choice, text). Without a saved choice the candidate is used; empty text means no title."""
    saved = titles.get(str(project.get('project_id'))) or {}
    choice = saved.get('choice') if saved.get('choice') in CHOICES else 'candidate'
    if 'text' in saved:
        return choice, str(saved['text']).strip()
    return choice, (candidate(project, topology, links) if choice == 'candidate' else str(project.get('name', '')))


# ------------------------------------------------------------------ layout

def _style(node):
    style = ((node.get('label') or {}).get('style') or '')
    family = re.search(r'font-family:\s*([^;]+)', style)
    size = re.search(r'font-size:\s*([\d.]+)', style)
    fill = re.search(r'fill:\s*(#[0-9a-fA-F]{6})', style)
    return {'family': family[1].strip() if family else LABEL_FONT['family'],
            'size': float(size[1]) if size else LABEL_FONT['size'],
            'bold': 'bold' in style or not style, 'color': fill[1] if fill else LABEL_FONT['color']}


def layout(project, topology, width, height, calibration, title, configs=None, show=None):
    """Drawing request for annotate-topology.ps1 (picture coordinates)."""
    links = link_values(project, topology, configs, show)
    font = _style(next(iter(topology.get('nodes', [])), {})) if topology.get('nodes') else dict(LABEL_FONT)
    spec = {'schema': 'topolog-annotation-spec-v1', 'width': width, 'height': height, 'label_font': font,
            'title_font': dict(TITLE_FONT), 'title': title or '', 'items': [], 'obstacles': [],
            'segments': [], 'legend': []}
    for item in links:
        if item['lines']:
            spec['legend'].append('{} {} – {} {}  {}'.format(item['names'][0], item['ports'][0], item['names'][1],
                                                            item['ports'][1], '  '.join(item['lines'])))
    if not calibration or not calibration.get('certain'):
        spec['mode'] = 'legend'
        return spec, links
    spec['mode'] = 'on_image'
    ox, oy = calibration['offset']
    nodes = [n for n in topology.get('nodes', []) if n.get('x') is not None]
    centers = {}
    for n in nodes:
        w, h = float(n.get('width') or 60), float(n.get('height') or 45)
        x, y = float(n['x']) + ox, float(n['y']) + oy
        centers[n['node_id']] = (x + w / 2, y + h / 2)
        spec['obstacles'].append([x - 2, y - 2, w + 4, h + 4])
        label = n.get('label') or {}
        if label.get('text'):
            spec['obstacles'].append([x + float(label.get('x', 0)) - 2, y + float(label.get('y', 0)) - 2,
                                      len(label['text']) * 8 + 4, 18])
    cx = sum(c[0] for c in centers.values()) / max(1, len(centers))
    cy = sum(c[1] for c in centers.values()) / max(1, len(centers))
    for item in links:
        a, b = (centers.get(e['node_id']) for e in item['ends'])
        if not a or not b:
            continue
        spec['segments'].append([a[0], a[1], b[0], b[1]])
        for end, label in zip(item['ends'], item['labels']):
            if label.get('text'):
                x, y = centers[end['node_id']]
                w, h = float(end.get('width') or 60), float(end.get('height') or 45)
                spec['obstacles'].append([x - w / 2 + float(label.get('x', 0)) - 2, y - h / 2 + float(label.get('y', 0)) - 2,
                                          len(label['text']) * 8 + 4, 18])
    for item in links:
        if not item['lines']:
            continue
        a, b = (centers.get(e['node_id']) for e in item['ends'])
        if not a or not b:
            continue
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        dx, dy = b[1] - a[1], -(b[0] - a[0])
        length = (dx * dx + dy * dy) ** .5 or 1
        dx, dy = dx / length, dy / length
        if (mx + dx - cx) ** 2 + (my + dy - cy) ** 2 < (mx - dx - cx) ** 2 + (my - dy - cy) ** 2:
            dx, dy = -dx, -dy
        spec['items'].append({'text': '\n'.join(item['lines']), 'anchor': [mx, my], 'direction': [dx, dy], 'gap': 16})
    return spec, links


# ------------------------------------------------------------------ build

def plain_path(path):
    text = str(path)
    if text.startswith('\\\\?\\UNC\\'):
        return '\\\\' + text[8:]
    if text.startswith('\\\\?\\'):
        return text[4:]
    return os.path.abspath(text)


def render_windows(jobs):
    """Draw every job with System.Drawing in one PowerShell process; {output: result or error text}."""
    if os.name != 'nt':
        return {job['output']: 'Annotated pictures are drawn on Windows' for job in jobs}
    if not jobs:
        return {}
    from .resources import tool_directory
    import tempfile
    with tempfile.TemporaryDirectory(prefix='topolog-annotate-') as temp:
        request = Path(temp) / 'jobs.json'
        request.write_text(json.dumps(jobs, ensure_ascii=False), encoding='utf-8')
        try:
            completed = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-File',
                                        str(tool_directory() / 'annotate-topology.ps1'), '-Jobs', str(request)],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30 + 10 * len(jobs),
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {job['output']: str(exc)[:200] for job in jobs}
        failure = completed.stderr.decode('utf-8', 'replace').strip()[-300:]
    results = {}
    for job in jobs:
        record = Path(job['output']).with_name('annotation-render.json')
        try:
            data = json.loads(record.read_text(encoding='utf-8-sig'))
            results[job['output']] = data if data.get('status') == 'drawn' and Path(job['output']).is_file() \
                else (data.get('error') or 'annotate-topology.ps1 did not draw the picture')
        except (OSError, ValueError):
            results[job['output']] = failure or 'annotate-topology.ps1 did not run'
    return results


def prepare(folder, project, titles=None, configs=None):
    """(record, job): job is None when nothing should be drawn for this project."""
    folder = Path(folder)
    from .resultdir import records
    base = records(folder) / str(project.get('project_id', ''))
    native = (project.get('topology_images') or {}).get('native') or {}
    record = {'kind': 'topolog_annotated_copy', 'source': native.get('file')}
    source = base / 'capture' / 'gns3-topology.png'
    if native.get('status') != 'captured' or not source.is_file():
        record.update(status='unavailable', reason='GNS3 자체 사진이 없어 자동 주석을 만들지 않았습니다.')
        return record, None
    try:
        topology = json.loads((base / 'topology.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        record.update(status='unavailable', reason='토폴로지 기록(topology.json)을 읽지 못했습니다.')
        return record, None
    written = user_text(topology)
    if written:
        record.update(status='skipped', reason='GNS3 프로젝트에 직접 입력한 글자가 있어 자동 주석을 넣지 않았습니다.',
                      user_text=written[:6])
        return record, None
    try:
        width, height, rows = read_png(source.read_bytes())
        calibration = calibrate(topology.get('nodes', []), Ink(width, height, rows))
    except (ValueError, zlib.error, KeyError, struct.error) as exc:
        width = height = 0
        calibration = {'certain': False, 'error': str(exc)}
    show = shown(titles, project)
    links = link_values(project, topology, configs, show)
    choice, title = resolve_title(project, topology, titles or {}, links)
    if not any(i['lines'] for i in links) and not title:
        if any(i['all_lines'] for i in links):
            record.update(status='skipped', reason='사진 제목 정리에서 넣을 주석과 제목을 모두 끄거나 비워 자동 주석 그림을 만들지 않았습니다.', show=show)
        else:
            record.update(status='skipped', reason='주석으로 넣을 서브넷·cost 값을 수집한 설정에서 찾지 못했습니다.')
        return record, None
    spec, _ = layout(project, topology, width, height, calibration, title, configs, show)
    record.update(file=FILE, mode=spec['mode'], title=title, title_choice=choice, calibration=calibration,
                  captions=[i['lines'] for i in links if i['lines']], show=show)
    # Plain absolute paths: a resolved Windows path can carry the \\?\ prefix, which Windows PowerShell's
    # System.Drawing rejects ("path format is not supported", 15-03-12 run).
    return record, {'spec': spec, 'source': plain_path(source), 'output': plain_path(base / FILE)}


def build_all(folder, projects, titles=None, renderer=None):
    """Annotated copies for many projects with one drawing process; records are also written to disk."""
    from .resultdir import records
    folder = Path(folder)
    data = records(folder)
    prepared = [(project, *prepare(folder, project, titles)) for project in projects
                if (project.get('topology_images') or {}).get('native')]
    jobs = [job for _, _, job in prepared if job]
    results = (renderer or render_windows)(jobs) if jobs else {}
    records = {}
    for project, record, job in prepared:
        if job:
            outcome = results.get(job['output'])
            if isinstance(outcome, dict):
                record.update(status='generated', render=outcome)
            else:
                record.pop('file', None)
                record.update(status='unavailable', reason='자동 주석 그림을 만들지 못했습니다: ' + str(outcome or '')[:200])
        _write_record(data / str(project.get('project_id', '')), record)
        project.setdefault('topology_images', {})['annotated'] = summary_entry(record)
        records[project.get('project_id')] = record
    return records


def build(folder, project, titles=None, renderer=None):
    return build_all(folder, [project], titles, renderer).get(project.get('project_id'))


def _write_record(base, record):
    target = base / RECORD
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')


def summary_entry(record):
    keep = ('status', 'file', 'mode', 'reason', 'title', 'title_choice')
    return {'kind': 'topolog_annotated_copy', **{k: record[k] for k in keep if k in record}}


def rebuild(folder, titles=None, renderer=None, progress=None):
    """Regenerate every annotated copy from saved files, then the reports. No GNS3 access."""
    folder = Path(folder).resolve(strict=True)
    from .resultdir import records, top
    folder = top(folder)
    report_file = records(folder) / 'summary.json'
    report = json.loads(report_file.read_text(encoding='utf-8'))
    if report.get('schema') != 'topolog-batch-v2' or not report.get('finished_at'):
        raise ValueError('완료된 TopoLog 일괄 결과 폴더를 선택하세요.')
    from .security import validate_report_paths
    validate_report_paths(report, folder)
    if titles is not None:
        save_titles(folder, titles)
    if progress:
        progress('자동 주석 그림을 다시 만듭니다.')
    build_all(folder, report.get('projects', []), load_titles(folder), renderer)
    temporary = report_file.with_name('summary.json.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, report_file)
    if progress:
        progress('보고서를 다시 만듭니다.')
    from .presentation import refresh_report
    # The collection-time report stays as it was; the annotated Word report is made separately.
    return refresh_report(folder, original_documents=False)


def overview(folder):
    """Rows for the title screen: project, photo path, features, candidate and saved choice."""
    from .resultdir import records, top
    folder = top(Path(folder))
    report = json.loads((records(folder) / 'summary.json').read_text(encoding='utf-8'))
    titles = load_titles(folder)
    from .features import LABELS
    rows = []
    for project in report.get('projects', []):
        native = (project.get('topology_images') or {}).get('native') or {}
        base = records(folder) / str(project.get('project_id', ''))
        try:
            topology = json.loads((base / 'topology.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            topology = {}
        links = link_values(project, topology)
        features = sorted({f for n in project.get('nodes', []) for f in (n.get('analysis') or {}).get('features', [])})
        saved = titles.get(str(project.get('project_id'))) or {}
        record = {}
        try:
            record = json.loads((base / RECORD).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
        rows.append({'project_id': project.get('project_id'), 'name': project.get('name', ''),
                     'photo': str(base / 'capture' / 'gns3-topology.png') if native.get('status') == 'captured' else None,
                     'annotated': str(base / FILE) if record.get('status') == 'generated' else None,
                     'features': [LABELS.get(f, f) for f in features],
                     'captions': [i['all_lines'] for i in links if i['all_lines']],
                     'show': shown(titles, project),
                     'candidate': candidate(project, topology, links),
                     'user_text': user_text(topology), 'choice': saved.get('choice', 'candidate'),
                     'text': saved.get('text'), 'status': record.get('status'), 'reason': record.get('reason')})
    return rows

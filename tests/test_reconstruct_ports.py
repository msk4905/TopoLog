"""Port names on the reconstructed topology drawing."""
from pathlib import Path
import re
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from topolog.topology import reconstruct, port_name


def boxes(svg):
    return [tuple(map(float, m)) for m in re.findall(r'<rect x="([-\d.]+)" y="([-\d.]+)" width="([\d.]+)" height="16"', svg)]


class PortLabelTests(unittest.TestCase):
    def test_each_link_end_gets_its_port_name_without_labels_overlapping(self):
        nodes = [{'node_id': n, 'name': n, 'x': x, 'y': y} for n, x, y in
                 (('R1', 0, 0), ('R2', 300, 0), ('R3', 150, 200), ('R4', 260, 110))]
        ends = lambda a, pa, b, pb: {'nodes': [{'node_id': a, 'label': {'text': pa}}, {'node_id': b, 'label': {'text': pb}}]}
        links = [ends('R1', 'f0/0', 'R2', 'f0/1'), ends('R1', 's1/0', 'R3', 's1/1'), ends('R2', 'Gi0/2', 'R3', 'Gi0/3'),
                 ends('R2', 'e0', 'R4', 'e1'), ends('R1', 'f1/0', 'R2', 'f1/1')]       # two links between R1 and R2
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'map.svg'
            reconstruct({'nodes': nodes, 'links': links}, target)
            svg = target.read_text(encoding='utf-8')
        for name in ('f0/0', 'f0/1', 's1/0', 's1/1', 'Gi0/2', 'Gi0/3', 'e0', 'e1', 'f1/0', 'f1/1'):
            self.assertIn('>' + name + '<', svg)
        labels = [(x, y, x + w, y + 16) for x, y, w in boxes(svg)]
        self.assertEqual(len(labels), 10)
        for i, a in enumerate(labels):
            for b in labels[i + 1:]:
                self.assertTrue(a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1], (a, b))

    def test_port_name_falls_back_to_adapter_and_port_numbers(self):
        self.assertEqual(port_name({'adapter_number': 1, 'port_number': 2}), '1/2')
        self.assertEqual(port_name({'label': {'text': ' s1/0 '}}), 's1/0')
        self.assertEqual(port_name({}), '')


if __name__ == '__main__':
    unittest.main()

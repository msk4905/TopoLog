import configparser
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from topolog.server_config import prepare, repair_dynamips, repair_vpcs, RestartRequired


class ServerConfigTests(unittest.TestCase):
    def fixture(self, root, text):
        server = root / 'GNS3' / 'gns3server.exe'
        helper = server.parent / 'dynamips' / 'dynamips.exe'
        helper.parent.mkdir(parents=True)
        server.touch()
        helper.touch()
        config = root / 'gns3_server.ini'
        config.write_bytes(text)
        return server, helper, config

    def test_repair_preserves_other_settings_and_backup_bytes(self):
        original = b'\xef\xbb\xbf; private config\r\n[Server]\r\nhost=127.0.0.1\r\npassword=a%b#c\r\n[Qemu]\r\nenable_kvm=false\r\n'
        with tempfile.TemporaryDirectory() as temp:
            server, helper, config = self.fixture(Path(temp), original)
            result = repair_dynamips(server, config)
            self.assertEqual(Path(result['backup']).read_bytes(), original)
            self.assertTrue(config.read_bytes().startswith(original))
            parsed = configparser.ConfigParser(interpolation=None)
            parsed.read(config, encoding='utf-8-sig')
            self.assertEqual(parsed.get('Server', 'password'), 'a%b#c')
            self.assertEqual(parsed.get('Dynamips', 'dynamips_path'), str(helper))
            self.assertIsNone(repair_dynamips(server, config))
            self.assertEqual(len(list(Path(temp).glob('*.bak'))), 1)

    def test_relative_helper_becomes_absolute_without_changing_next_section(self):
        with tempfile.TemporaryDirectory() as temp:
            original = b'[Dynamips]\ndynamips_path=dynamips\nallocate=512\n[Server]\nport=3080\n'
            server, helper, config = self.fixture(Path(temp), original)
            repair_dynamips(server, config)
            parsed = configparser.ConfigParser(interpolation=None)
            parsed.read(config)
            self.assertEqual(parsed.get('Dynamips', 'dynamips_path'), str(helper))
            self.assertEqual(parsed.get('Dynamips', 'allocate'), '512')
            self.assertEqual(parsed.get('Server', 'port'), '3080')

    def test_valid_custom_absolute_helper_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            server, helper, config = self.fixture(root, b'')
            custom = root / 'custom.exe'
            custom.touch()
            content = '[Dynamips]\ndynamips_path='+str(custom)+'\n'
            config.write_text(content)
            self.assertIsNone(repair_dynamips(server, config))
            self.assertEqual(config.read_text(), content)
            self.assertFalse(list(root.glob('*.bak')))

    def test_missing_helper_and_malformed_config_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            server, helper, config = self.fixture(Path(temp), b'[Server]\nport=3080\n')
            helper.unlink()
            original = config.read_bytes()
            with self.assertRaises(ValueError):
                repair_dynamips(server, config)
            self.assertEqual(config.read_bytes(), original)
            config.write_bytes(b'not an ini file')
            with self.assertRaises(configparser.Error):
                repair_dynamips(server, config)
            self.assertEqual(config.read_bytes(), b'not an ini file')

    def test_remote_and_vm_only_runs_never_edit_local_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            entry = {'status': 'pending', 'file': 'project.gns3'}
            path = root / entry['file']
            api = Mock(base='http://remote:3080', host='remote')
            for node in ({'node_type':'dynamips','compute_id':'vm'}, {'node_type':'dynamips','compute_id':'local'}):
                path.write_text(json.dumps({'topology':{'nodes':[node]}}))
                with patch('topolog.transport.API', return_value=Mock(base='http://localhost:3080')), patch('topolog.server_config.repair_dynamips') as repair:
                    prepare(api, root, [entry])
                    repair.assert_not_called()

    def test_changed_local_config_stops_before_collection_for_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / 'project.gns3'
            path.write_text(json.dumps({'topology':{'nodes':[{'node_type':'dynamips','compute_id':'local'}]}}))
            api = Mock(base='http://localhost:3080', host='localhost')
            api.call.return_value = [{'compute_id':'local','host':'localhost'}]
            with patch('topolog.transport.API', return_value=api), patch('topolog.server_config.backend.discover', return_value=({'localhost'},[root/'gns3server.exe'])), patch('topolog.server_config.repair_dynamips', return_value={'dynamips_path':'bundled helper','backup':'private backup'}) as repair:
                with self.assertRaises(RestartRequired):
                    prepare(api, root, [{'status':'pending','file':path.name}])
                repair.assert_called_once()
            self.assertEqual([call.args[0] for call in api.call.call_args_list], ['/v2/computes'])

    def local_api(self, version):
        api = Mock(base='http://localhost:3080', host='localhost')
        def call(path, *args):
            return {'/v2/computes': [{'compute_id': 'local', 'host': 'localhost'}], '/v2/version': version}[path]
        api.call.side_effect = call
        return api

    def run_prepare(self, root, nodes, version, config_text=b'[Server]\nport=3080\n'):
        server, helper, config = self.fixture(root, config_text)
        (server.parent / 'vpcs').mkdir()
        (server.parent / 'vpcs' / 'vpcs.exe').touch()
        path = root / 'project.gns3'
        path.write_text(json.dumps({'topology': {'nodes': nodes}}))
        api = self.local_api(version)
        with patch('topolog.transport.API', return_value=api), patch('topolog.server_config.backend.discover', return_value=({'localhost'}, [server])):
            prepare(api, root, [{'status': 'pending', 'file': path.name, 'name': 'lab'}], config_path=config)
        return api, config

    def test_vpcs_path_is_repaired_with_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = b'[Server]\r\nport=3080\r\n[Dynamips]\r\ndynamips_path=x\r\n'
            server, helper, config = self.fixture(root, original)
            (server.parent / 'vpcs').mkdir()
            vpcs = server.parent / 'vpcs' / 'vpcs.exe'
            vpcs.touch()
            result = repair_vpcs(server, config)
            self.assertEqual(Path(result['backup']).read_bytes(), original)
            parsed = configparser.ConfigParser(interpolation=None)
            parsed.read(config, encoding='utf-8-sig')
            self.assertEqual(parsed.get('VPCS', 'vpcs_path'), str(vpcs))
            self.assertEqual(parsed.get('Dynamips', 'dynamips_path'), 'x')
            self.assertIn(b'\r\n[VPCS]\r\nvpcs_path = ', config.read_bytes())
            self.assertIsNone(repair_vpcs(server, config))

    def test_local_vpcs_nodes_trigger_one_restart_for_both_helpers(self):
        with tempfile.TemporaryDirectory() as temp:
            nodes = [{'node_type': 'dynamips', 'compute_id': 'local', 'name': 'ESW1'},
                     {'node_type': 'vpcs', 'compute_id': 'local', 'name': 'PC1'}]
            with self.assertRaises(RestartRequired) as caught:
                self.run_prepare(Path(temp), nodes, {'local': True})
            self.assertIn('Dynamips', str(caught.exception))
            self.assertIn('VPCS', str(caught.exception))
            self.assertEqual(len(list(Path(temp).glob('*.bak'))), 2)

    def test_non_local_server_with_outside_absolute_image_requires_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            images = root / 'images'
            outside = '/documents/c7200.bin'
            config_text = '[Server]\nimages_path={}\n[Dynamips]\ndynamips_path={}\n'.format(images, root / 'GNS3' / 'dynamips' / 'dynamips.exe').encode()
            nodes = [{'node_type': 'dynamips', 'compute_id': 'local', 'name': 'R1', 'properties': {'image': outside}}]
            with self.assertRaises(RestartRequired) as caught:
                self.run_prepare(root, nodes, {'local': False}, config_text)
            self.assertIn('local=false', str(caught.exception))
            self.assertIn('gns3server.exe', str(caught.exception))
            self.assertFalse(list(root.glob('*.bak')))

    def test_image_inside_images_folder_or_local_server_is_not_blocked(self):
        for version, image_inside in (({'local': False}, True), ({'local': True}, False)):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                images = root / 'images'
                image = str(images / 'IOS' / 'c7200.bin') if image_inside else '/documents/c7200.bin'
                config_text = '[Server]\nimages_path={}\n[Dynamips]\ndynamips_path={}\n'.format(images, root / 'GNS3' / 'dynamips' / 'dynamips.exe').encode()
                nodes = [{'node_type': 'dynamips', 'compute_id': 'local', 'name': 'R1', 'properties': {'image': image}}]
                api, config = self.run_prepare(root, nodes, version, config_text)
                self.assertEqual(config.read_bytes(), config_text)


if __name__ == '__main__':
    unittest.main()

"""Drive real Tk widgets with synthetic device services, never user GNS3 state."""
import json
import gc
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from topolog import gui
from topolog.engine import Runner
from test_batch import FakeAPI, collected, project, P, N


class GuiTests(unittest.TestCase):
    def setUp(self):
        # Tk variables from destroyed test windows must be finalized on this
        # thread, not by a later collection worker's cyclic-GC pass.
        gc.collect()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.labs = self.root / 'labs'
        self.labs.mkdir()
        project(self.labs, 'routing', P)
        self.defaults = patch('topolog.gui.settings.load', return_value={
            'project_directory':str(self.labs), 'output_directory':str(self.root/'exports')})
        self.defaults.start()
        self.save_settings = patch('topolog.gui.settings.save').start()
        patch('topolog.topology.capture_native',return_value={'status':'unavailable','reason':'GUI test fixture'}).start()
        # The fixture GNS3 is already prepared; the real check is covered by test_preflight.
        self.preflight = patch('topolog.gui.preflight.prepare', return_value={'gui_running': True}).start()
        patch('topolog.gui.preflight.gui_running', return_value=True).start()
        self.errors = patch('topolog.gui.messagebox.showerror').start()
        self.browser = patch('topolog.gui.webbrowser.open', return_value=True).start()
        patch('topolog.gui.open_report', side_effect=lambda p: gui.webbrowser.open(p.as_uri())).start()
        self.window = gui.launch(run_loop=False)
        self.window.update_idletasks()
        self.ui = self.window._topolog

    def tearDown(self):
        self.window.update_idletasks()
        for timer in self.window.tk.call('after', 'info'):
            self.window.after_cancel(timer)
        self.window.destroy()
        self.ui.clear()
        gc.collect()
        patch.stopall()
        self.temp.cleanup()

    def finish_worker(self):
        deadline = time.monotonic()+4
        while self.ui['state']['running'] and time.monotonic()<deadline:
            self.window.update()
            time.sleep(.01)
        self.assertFalse(self.ui['state']['running'])
        self.errors.assert_not_called()

    def test_scan_collect_and_open_result_through_widgets(self):
        api = FakeAPI()
        def runner(*args, **kwargs):
            return Runner(*args, **kwargs, collector=collected, settle_seconds=0)
        with patch('topolog.gui.connect', return_value=api), patch('topolog.gui.prepare_server'), patch('topolog.gui.Runner', side_effect=runner):
            self.ui['actions']['scan'].invoke()
            self.ui['actions']['run'].invoke()
            self.assertTrue(self.ui['state']['running'])
            self.assertTrue(self.ui['actions']['edit'].instate(['disabled']))
            self.finish_worker()
            self.assertTrue(self.ui['actions']['run'].instate(['!disabled']))
            result = self.ui['state']['result']
            self.assertTrue((result/'index.html').is_file())
            self.browser.assert_called_once_with((result/'index.html').resolve().as_uri())
            with patch('topolog.gui.webbrowser.open') as browser:
                self.ui['result'].invoke()
                browser.assert_called_once_with((result/'index.html').resolve().as_uri())

    def test_password_request_is_masked_and_not_saved(self):
        def collector(*args,**kwargs):
            self.assertEqual(kwargs['password_provider']('ASA'),'fixture-secret')
            return collected(*args,**kwargs)
        def runner(*args,**kwargs):return Runner(*args,**kwargs,collector=collector,settle_seconds=0)
        with patch('topolog.gui.simpledialog.askstring',return_value='fixture-secret') as prompt,patch('topolog.gui.connect',return_value=FakeAPI()),patch('topolog.gui.prepare_server'),patch('topolog.gui.Runner',side_effect=runner):
            self.ui['actions']['run'].invoke()
            self.finish_worker()
            self.assertEqual(prompt.call_args.kwargs['show'],'*')
            self.assertNotIn('fixture-secret',(self.ui['state']['result']/'summary.json').read_text(encoding='utf-8'))

    def test_default_enable_password_is_passed_but_never_saved(self):
        seen = {}
        def runner(*args, **kwargs):
            seen.update(kwargs)
            return Runner(*args, **kwargs, collector=collected, settle_seconds=0)
        self.ui['default_password'].set('fixture-default')
        with patch('topolog.gui.connect', return_value=FakeAPI()), patch('topolog.gui.prepare_server'), patch('topolog.gui.Runner', side_effect=runner):
            self.ui['actions']['run'].invoke()
            self.finish_worker()
        self.assertEqual(seen['enable_password'], 'fixture-default')
        self.assertNotIn('fixture-default', repr(self.save_settings.call_args))
        self.assertNotIn('fixture-default', (self.ui['state']['result']/'summary.json').read_text(encoding='utf-8'))
        self.preflight.assert_called_once()

    def test_gns3_not_running_asks_when_auto_start_is_off(self):
        self.ui['auto_start'].set(False)
        with patch('topolog.gui.preflight.gui_running', return_value=False), \
             patch('topolog.gui.messagebox.askyesnocancel', return_value=None) as ask, patch('topolog.gui.Runner') as runner:
            self.ui['actions']['run'].invoke()
        ask.assert_called_once()
        runner.assert_not_called()
        self.assertFalse(self.ui['state']['running'])
        self.assertIs(self.save_settings.call_args.kwargs['auto_start_gns3'], False)

    def test_screenshot_checkbox_controls_capture(self):
        seen = {}
        def runner(*args, **kwargs):
            seen.update(kwargs)
            return Runner(*args, **kwargs, collector=collected, settle_seconds=0)
        self.ui['capture'].set(False)
        with patch('topolog.gui.connect', return_value=FakeAPI()), patch('topolog.gui.prepare_server'), patch('topolog.gui.Runner', side_effect=runner):
            self.ui['actions']['run'].invoke()
            self.finish_worker()
        self.assertIs(seen['capture'], False)
        self.assertIs(self.save_settings.call_args.kwargs['capture_screenshot'], False)

    def test_password_prompt_raises_window_to_front(self):
        def collector(*args, **kwargs):
            kwargs['password_provider']('ASA')
            return collected(*args, **kwargs)
        def runner(*args, **kwargs):
            return Runner(*args, **kwargs, collector=collector, settle_seconds=0)
        calls = []
        original = self.window.attributes
        def attributes(*args):
            calls.append(args)
            return original(*args)
        with patch.object(self.window, 'attributes', side_effect=attributes), \
             patch('topolog.gui.simpledialog.askstring', return_value=None), patch('topolog.gui.connect', return_value=FakeAPI()), \
             patch('topolog.gui.prepare_server'), patch('topolog.gui.Runner', side_effect=runner):
            self.ui['actions']['run'].invoke()
            self.finish_worker()
        self.assertIn(('-topmost', True), calls)
        self.assertIn(('-topmost', False), calls)

    def test_stop_button_waits_for_engine_cleanup_and_keeps_report(self):
        entered = threading.Event()
        api = FakeAPI()
        def collector(*args, **kwargs):
            entered.set()
            if not kwargs['cancel'].wait(2):
                raise AssertionError('GUI stop did not reach engine')
            return {'collection':'cancelled','reason':'Fixture stop','commands':[],
                    'checks':[{'status':'inconclusive','reason':'Cancelled'}]}
        def runner(*args, **kwargs):
            return Runner(*args, **kwargs, collector=collector, settle_seconds=0)
        with patch('topolog.gui.connect', return_value=api), patch('topolog.gui.prepare_server'), patch('topolog.gui.Runner', side_effect=runner):
            self.ui['actions']['run'].invoke()
            self.assertTrue(entered.wait(2))
            self.ui['stop'].invoke()
            self.assertTrue(self.ui['cancel'].is_set())
            self.finish_worker()
            report = json.loads((self.ui['state']['result']/'summary.json').read_text(encoding='utf-8'))
            self.assertTrue(report['cancelled'])
            self.browser.assert_not_called()
            self.assertEqual(api.states[next(k for k in api.states if k != N)], 'stopped')
            self.assertIn('중지됨',self.ui['status'].get())

    def test_invalid_criteria_never_connects(self):
        path = self.root/'bad.json'
        path.write_text('{"schema":"wrong"}')
        self.ui['rules'].set(str(path))
        with patch('topolog.gui.connect') as connection:
            self.ui['actions']['run'].invoke()
            connection.assert_not_called()
        self.errors.assert_called_once()
        self.assertFalse(self.ui['state']['running'])

    def test_browser_failure_does_not_turn_saved_run_into_collection_error(self):
        def runner(*args, **kwargs):
            return Runner(*args, **kwargs, collector=collected, settle_seconds=0)
        self.browser.side_effect = OSError('fixture missing browser')
        with patch('topolog.gui.connect',return_value=FakeAPI()), patch('topolog.gui.prepare_server'), patch('topolog.gui.Runner',side_effect=runner):
            self.ui['actions']['run'].invoke()
            self.finish_worker()
        self.assertTrue((self.ui['state']['result']/'index.html').is_file())
        self.assertTrue(self.ui['result'].instate(['!disabled']))
        self.assertIn('완료',self.ui['status'].get())

    def test_edit_node_expectation_and_save_by_name(self):
        dialog = self.ui['edit']()
        self.assertIsNotNone(dialog)
        dialog.withdraw()
        editor = dialog._topolog
        item = next(iter(editor['targets']))
        editor['tree'].selection_set(item)
        editor['select']()
        editor['fields']['neighbors'].set('2.2.2.2')
        editor['add']()
        path = self.root/'edited.json'
        with patch('topolog.criteria_editor.filedialog.asksaveasfilename',return_value=str(path)):
            editor['save']()
        self.assertEqual(self.ui['rules'].get(),str(path))
        saved = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(saved['projects'][P]['nodes'][N]['checks'],
                         [{'kind':'ospf_neighbors','neighbors':['2.2.2.2']}])
        self.assertEqual(saved['projects'][P]['nodes'][N]['name'],'R1')

    def test_offline_reassessment_button_never_connects(self):
        source = self.root/'old'
        Runner(FakeAPI(),source,collector=collected,settle_seconds=0).run(self.labs)
        from topolog.engine import draft, save
        path = self.root/'criteria.json'
        save(path,draft(self.labs))
        self.ui['rules'].set(str(path))
        with patch('topolog.gui.filedialog.askdirectory',return_value=str(source)), patch('topolog.gui.connect') as connection:
            self.ui['actions']['reassess'].invoke()
            self.finish_worker()
            connection.assert_not_called()
        report = json.loads((self.ui['state']['result']/'summary.json').read_text(encoding='utf-8'))
        self.assertIn('assessment',report)
        self.assertEqual(report['verdict'],'inconclusive')


if __name__ == '__main__':
    unittest.main()

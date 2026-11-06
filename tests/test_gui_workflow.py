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
from topolog.resultdir import records as R  # noqa: E402
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
            self.assertNotIn('fixture-secret',(R(self.ui['state']['result'])/'summary.json').read_text(encoding='utf-8'))

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
        self.assertNotIn('fixture-default', (R(self.ui['state']['result'])/'summary.json').read_text(encoding='utf-8'))
        self.preflight.assert_called_once()

    def test_gns3_and_vm_auto_start_is_always_requested(self):
        def runner(*args, **kwargs):
            return Runner(*args, **kwargs, collector=collected, settle_seconds=0)
        self.assertNotIn('auto_start', self.ui)
        with patch('topolog.gui.connect', return_value=FakeAPI()), patch('topolog.gui.prepare_server'), patch('topolog.gui.Runner', side_effect=runner):
            self.ui['actions']['run'].invoke()
            self.finish_worker()
        self.assertIs(self.preflight.call_args.args[1], True)
        self.assertNotIn('auto_start_gns3', self.save_settings.call_args.kwargs)

    def test_annotation_checkbox_reaches_runner_and_title_tool_exists(self):
        seen = {}
        self.assertIn('titles', self.ui['actions'])
        self.assertTrue(self.ui['annotate'].get())
        self.ui['annotate'].set(False)
        with patch('topolog.gui.connect', return_value=FakeAPI()), patch('topolog.gui.prepare_server'), \
             patch('topolog.gui.Runner', side_effect=self.runner(seen)):
            self.ui['actions']['run'].invoke()
            self.finish_worker()
        self.assertIs(seen['annotate'], False)
        self.assertIs(self.save_settings.call_args.kwargs['annotate'], False)

    def test_every_check_box_shows_a_tick_not_the_theme_cross(self):
        from tkinter import ttk
        style = ttk.Style(self.window)
        for name in ('Panel.TCheckbutton', 'TCheckbutton'):        # wizard and the separate windows alike
            self.assertIn('TopoLog.check', str(style.layout(name)))

    def test_title_editor_opens_after_collection_and_report_after_it_closes(self):
        import tkinter as tk
        from tkinter import ttk
        def texts(widget):
            found = []
            for child in widget.winfo_children():
                if isinstance(child, ttk.Button):
                    found.append(child.cget('text'))
                found += texts(child)
            return found
        self.assertNotIn('기존 결과의 사진 제목 정리', texts(self.window))      # no title step before pictures exist

        class Marked(Runner):
            def run(self, root):
                result = super().run(root)
                for p in result['projects']:
                    p.setdefault('topology_images', {})['annotated'] = {'status': 'generated'}
                return result
        opened = []
        def editor(parent, folder, **kwargs):
            opened.append(Path(folder))
            return tk.Toplevel(parent)
        with patch('topolog.gui.connect', return_value=FakeAPI()), patch('topolog.gui.prepare_server'), \
             patch('topolog.gui.Runner', side_effect=lambda *a, **k: Marked(*a, **k, collector=collected, settle_seconds=0)), \
             patch('topolog.title_editor.open_editor', side_effect=editor):
            self.ui['actions']['run'].invoke()
            self.finish_worker()
            self.assertEqual(opened, [self.ui['state']['result']])
            self.browser.assert_not_called()                                    # report waits for the titles
            self.ui['state']['title_editor'].destroy()
            self.window.update()
            self.browser.assert_called_once_with((self.ui['state']['result'] / 'index.html').resolve().as_uri())

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
            report = json.loads((R(self.ui['state']['result'])/'summary.json').read_text(encoding='utf-8'))
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
        report = json.loads((R(self.ui['state']['result'])/'summary.json').read_text(encoding='utf-8'))
        self.assertIn('assessment',report)
        self.assertEqual(report['verdict'],'inconclusive')


    # Setup pages, settings handed to the Runner, plain progress and pause.
    def paused_runner(self, collector):
        build = self.runner(collector=collector)
        def paused(*args, **kwargs):
            self.ui['gate'].request()                     # worker thread, after start_worker resets the gate
            return build(*args, **kwargs)
        return paused

    def runner(self, seen=None, collector=collected):
        def build(*args, **kwargs):
            if seen is not None:
                seen.update(kwargs)
            return Runner(*args, **kwargs, collector=collector, settle_seconds=0)
        return build

    def test_pages_move_forward_and_back_and_summary_reflects_choices(self):
        go, pages = self.ui['go'], self.ui['pages']
        self.assertEqual(len(pages), 5)
        self.assertTrue(pages[0].winfo_manager())
        self.ui['actions']['next'].invoke()
        self.assertEqual(self.ui['state']['step'], 1)
        self.assertIn('1개 확인됨', self.ui['folder_message'].get())
        self.ui['default_password'].set('fixture-secret')
        self.ui['capture'].set(False)
        self.ui['detail_scope'].set('all')
        for _ in range(3):
            self.ui['actions']['next'].invoke()
        self.assertEqual(self.ui['state']['step'], 4)
        summary = {k: v.get() for k, v in self.ui['summary'].items()}
        self.assertEqual(summary['토폴로지 사진'], '재구성 그림만')
        self.assertIn('전체 프로젝트', summary['보고서 범위'])
        self.assertIn('입력됨', summary['enable 권한'])
        self.assertNotIn('fixture-secret', repr(summary))
        self.ui['actions']['back'].invoke()
        self.assertEqual(self.ui['state']['step'], 3)

    def test_queued_next_click_during_scan_does_not_skip_enable_page(self):
        original = gui.discover
        def scan_with_queued_click(*args, **kwargs):
            self.window.after_idle(self.ui['actions']['next'].invoke)
            return original(*args, **kwargs)
        with patch('topolog.gui.discover', side_effect=scan_with_queued_click):
            self.ui['actions']['next'].invoke()
        self.assertEqual(self.ui['state']['step'], 1)

    def test_summary_folder_labels_wrap_across_the_full_row(self):
        self.ui['go'](4)
        variables = list(self.ui['summary'].values())[:2]
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        for variable in variables:
            labels = [w for w in descendants(self.window)
                      if 'textvariable' in w.keys() and str(w.cget('textvariable')) == str(variable)]
            self.assertEqual(len(labels), 1)
            self.assertEqual(int(labels[0].cget('wraplength')), 680)
            self.assertEqual(int(labels[0].grid_info()['columnspan']), 3)

    def test_invalid_folder_blocks_the_first_page(self):
        self.ui['projects'].set(str(self.root / 'missing'))
        self.ui['actions']['next'].invoke()
        self.assertEqual(self.ui['state']['step'], 0)
        self.errors.assert_called_once()
        self.errors.reset_mock()

    def test_project_list_shows_names_not_internal_paths_and_scope_reaches_runner(self):
        seen = {}
        self.ui['detail_scope'].set('all')
        self.ui['go'](4)
        tree = self.ui['tree']
        rows = [tree.item(i) for i in tree.get_children()]
        self.assertEqual([r['text'] for r in rows], ['routing'])
        self.assertNotIn('.gns3', repr(rows))
        self.assertNotIn('pending', repr(rows))
        with patch('topolog.gui.connect', return_value=FakeAPI()), patch('topolog.gui.prepare_server'), \
             patch('topolog.gui.Runner', side_effect=self.runner(seen)):
            self.ui['actions']['run'].invoke()
            self.finish_worker()
        self.assertEqual(seen['detail_scope'], 'all')
        self.assertEqual(self.save_settings.call_args.kwargs['detail_scope'], 'all')
        report = json.loads((R(self.ui['state']['result']) / 'summary.json').read_text(encoding='utf-8'))
        self.assertEqual(report['report_options']['detail_scope'], 'all')
        self.assertEqual(tree.item(P)['values'][0], '완료')
        self.assertIn('처리한 프로젝트 1개', self.ui['outcome'].get())
        log = '\n'.join(self.ui['state']['log'])
        self.assertEqual(log.count('프로젝트 폴더 확인'), 1)          # not repeated at start

    def test_disabled_enable_never_passes_a_password(self):
        seen = {}
        self.ui['allow_enable'].set(False)
        self.ui['default_password'].set('fixture-secret')
        with patch('topolog.gui.connect', return_value=FakeAPI()), patch('topolog.gui.prepare_server'), \
             patch('topolog.gui.Runner', side_effect=self.runner(seen)):
            self.ui['actions']['run'].invoke()
            self.finish_worker()
        self.assertIs(seen['enable'], False)
        self.assertIsNone(seen['enable_password'])

    def test_pause_waits_at_a_safe_point_and_stop_is_accepted_while_paused(self):
        reached = threading.Event()
        def collector(*args, **kwargs):
            reached.set()
            return collected(*args, **kwargs)
        self.ui['actions']['pause'].configure(state='normal')
        with patch('topolog.gui.connect', return_value=FakeAPI()), patch('topolog.gui.prepare_server'), \
             patch('topolog.gui.Runner', side_effect=self.paused_runner(collector)):
            self.ui['actions']['run'].invoke()             # the pause is requested before the first safe point
            deadline = time.monotonic() + 2
            while not self.ui['tracker'].paused and time.monotonic() < deadline:
                self.window.update(); time.sleep(.01)
            self.assertTrue(self.ui['tracker'].paused)
            self.assertFalse(reached.is_set())
            self.assertIn('일시중지', self.ui['status'].get())
            self.ui['stop'].invoke()
            self.finish_worker()
        self.assertFalse(reached.is_set())
        self.assertIn('중지됨', self.ui['status'].get())

    def test_pause_then_resume_completes(self):
        with patch('topolog.gui.connect', return_value=FakeAPI()), patch('topolog.gui.prepare_server'), \
             patch('topolog.gui.Runner', side_effect=self.runner()):
            self.ui['actions']['run'].invoke()
            self.ui['gate'].request()
            self.ui['actions']['pause'].invoke()          # second press resumes
            self.finish_worker()
        self.assertIn('완료', self.ui['status'].get())

    def test_detailed_log_is_kept_in_a_separate_window_and_file(self):
        with patch('topolog.gui.connect', return_value=FakeAPI()), patch('topolog.gui.prepare_server'), \
             patch('topolog.gui.Runner', side_effect=self.runner()), \
             patch('topolog.gui.log_directory', return_value=self.root / 'logs'):
            self.ui['actions']['run'].invoke()
            self.finish_worker()
        files = list((self.root / 'logs').glob('run-*.log'))
        self.assertEqual(len(files), 1)
        self.assertEqual(len([w for w in self.window.winfo_children()
                             if isinstance(w, gui.tk.Toplevel)]), 1)
        self.assertIn('결과:', files[0].read_text(encoding='utf-8'))
        self.ui['show_log']()
        self.assertEqual(len([w for w in self.window.winfo_children()
                             if isinstance(w, gui.tk.Toplevel)]), 1)


if __name__ == '__main__':
    unittest.main()

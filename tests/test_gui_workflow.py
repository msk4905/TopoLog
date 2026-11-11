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
Q = '00000000-0000-0000-0000-0000000000a2'


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
        self.ask_retake = patch('topolog.gui.messagebox.askyesno', return_value=False).start()   # 'save pictures again?' after a run
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

    def test_picture_page_has_one_choice_and_missing_pictures_are_offered_again_after_the_run(self):
        seen = {}
        self.assertIn('titles', self.ui['actions'])
        self.assertNotIn('annotate', self.ui)                  # annotation copies are always made; titles come later
        self.assertNotIn('retry_photos', self.ui)
        retaken = []
        def retake(api, folder, root, **kwargs):
            retaken.append(Path(folder))
            report = json.loads((R(folder) / 'summary.json').read_text(encoding='utf-8'))
            return report
        self.ask_retake.return_value = True
        with patch('topolog.gui.connect', return_value=FakeAPI()), patch('topolog.gui.prepare_server'), \
             patch('topolog.gui.Runner', side_effect=self.runner(seen)), patch('topolog.engine.retake_pictures', side_effect=retake):
            self.ui['actions']['run'].invoke()
            self.finish_worker()                                    # run, question, then the retake worker
            deadline = time.monotonic() + 4
            while (self.ui['state']['running'] or not retaken) and time.monotonic() < deadline:
                self.window.update(); time.sleep(.01)
            self.finish_worker()
        self.assertIs(seen['annotate'], True)
        self.assertNotIn('retry_photos', seen)
        self.assertEqual(self.ask_retake.call_count, 1)            # asked once, not again after the retake
        self.assertIn('다시 저장', self.ask_retake.call_args.args[0])
        self.assertEqual(retaken, [self.ui['state']['result']])
        self.assertTrue(self.ui['actions']['retake'].instate(['!disabled']))   # still missing: the button stays available

    def test_projects_are_ticked_one_by_one_and_only_ticked_ones_run(self):
        project(self.labs, 'switching', Q)
        seen = {}
        self.ui['go'](4)
        tree = self.ui['tree']
        self.assertEqual(sorted(tree.get_children()), sorted([P, Q]))
        self.assertIn('2/2개', self.ui['pick_text'].get())
        tree.focus(Q)
        self.ui['toggle_project']()                             # space bar on the focused row
        self.assertEqual(self.ui['state']['selected'], {P})
        self.assertEqual(tree.item(Q)['values'][0], '선택 안 함')
        self.assertIn('1/2개', self.ui['pick_text'].get())
        with patch('topolog.gui.connect', return_value=FakeAPI()), patch('topolog.gui.prepare_server'), \
             patch('topolog.gui.Runner', side_effect=self.runner(seen)):
            self.ui['actions']['run'].invoke()
            self.finish_worker()
        self.assertEqual(seen['project_ids'], {P})
        report = json.loads((R(self.ui['state']['result']) / 'summary.json').read_text(encoding='utf-8'))
        self.assertEqual([p['project_id'] for p in report['projects']], [P])
        self.ui['pick_none'].invoke()
        self.assertEqual(self.ui['state']['selected'], set())
        with patch('topolog.gui.connect') as connection:
            self.ui['actions']['run'].invoke()
            connection.assert_not_called()                      # nothing ticked: nothing starts
        self.errors.assert_called_once()
        self.errors.reset_mock()
        self.ui['pick_all'].invoke()
        self.assertIsNone(self.ui['state']['selected'])

    def test_run_error_opens_the_report_window_and_sends_only_after_agreement(self):
        def runner(*args, **kwargs):
            raise RuntimeError('fixture failure')
        with patch('topolog.gui.connect', return_value=FakeAPI()), patch('topolog.gui.prepare_server'), \
             patch('topolog.gui.Runner', side_effect=runner), patch('topolog.errorreport.send', return_value='abc') as send, \
             patch('topolog.gui.log_directory', return_value=self.root / 'logs'):
            self.ui['actions']['run'].invoke()
            deadline = time.monotonic() + 4
            while self.ui['state']['running'] and time.monotonic() < deadline:
                self.window.update(); time.sleep(.01)
            top = self.ui['state']['report_window']
            buttons = {}
            def walk(widget):
                for child in widget.winfo_children():
                    if child.winfo_class() == 'TButton':
                        buttons[child.cget('text')] = child
                    if child.winfo_class() == 'TCheckbutton':
                        buttons['agree'] = child
                    walk(child)
            walk(top)
            self.assertTrue(buttons['보내기'].instate(['disabled']))
            buttons['보내기'].invoke()
            send.assert_not_called()                                   # no agreement, nothing sent
            buttons['agree'].invoke()
            buttons['보내기'].invoke()
            deadline = time.monotonic() + 2
            while not send.called and time.monotonic() < deadline:
                self.window.update(); time.sleep(.01)
            send.assert_called_once()
            self.assertIn('fixture failure', send.call_args.args[0]['preview'])
            self.assertTrue(list((self.root / 'logs').glob('error-report-*.txt')))
            for _ in range(5):
                self.window.update(); time.sleep(.12)                 # let the result reach the window
            top.destroy()
            self.window.update()
        self.errors.reset_mock()

    def widgets(self, top):
        found = {}
        def walk(widget):
            for child in widget.winfo_children():
                if child.winfo_class() in ('TButton', 'TCheckbutton'):
                    found[child.cget('text')] = child
                if child.winfo_class() == 'Text':
                    found.setdefault('texts', []).append(child)
                walk(child)
        walk(top)
        return found

    def test_button_handler_error_opens_one_report_window(self):
        def broken():
            raise KeyError('fixture handler bug')
        self.window.report_callback_exception(KeyError, KeyError('fixture handler bug'), None)
        top = self.ui['state']['report_window']
        self.assertEqual(top.title(), '오류 보고')
        preview = self.widgets(top)['texts'][0].get('1.0', 'end')
        self.assertIn('fixture handler bug', preview)
        self.ui['report_error']('second error')                         # no second window
        self.assertIs(self.ui['state']['report_window'], top)
        self.assertIn('second error', '\n'.join(self.ui['state']['log']))
        top.destroy()

    def test_feedback_needs_text_and_can_leave_the_log_out(self):
        with patch('topolog.errorreport.send', return_value='fb1') as send, \
             patch('topolog.gui.log_directory', return_value=self.root / 'logs'):
            top = self.ui['report_error'](feedback=True)
            self.assertEqual(top.title(), '피드백 보내기')
            found = self.widgets(top)
            comment, preview = found['texts']
            found['위 내용을 개발자에게 보내는 데 동의합니다'].invoke()
            found['보내기'].invoke()
            send.assert_not_called()                                    # empty text is not sent
            comment.insert('1.0', '사진 제목 창이 작아요')
            found['상세 실행 기록 함께 보내기(아래 내용)'].invoke()          # leave the log out
            self.assertIn('보내지 않습니다', preview.get('1.0', 'end'))
            found['보내기'].invoke()
            deadline = time.monotonic() + 2
            while not send.called and time.monotonic() < deadline:
                self.window.update(); time.sleep(.01)
            report = send.call_args.args[0]
            self.assertIn('사진 제목 창이 작아요', report['preview'])
            self.assertEqual(report['log'], '')
            self.assertEqual(report['event']['level'], 'info')
            for _ in range(5):
                self.window.update(); time.sleep(.12)
            top.destroy()

    def test_projects_sharing_an_id_are_announced_and_run_from_a_copy(self):
        (self.labs / 'copy').mkdir()
        project(self.labs / 'copy', 'routing-copy', P)              # same ID, copied folder
        with patch('topolog.gui.messagebox.askokcancel', return_value=False) as ask:
            self.ui['actions']['next'].invoke()
        self.assertEqual(self.ui['state']['step'], 0)                # cancel goes back
        self.assertIn('임시 폴더에 복사', ask.call_args.args[1])
        self.assertIn('routing', ask.call_args.args[1])
        with patch('topolog.gui.messagebox.askokcancel', return_value=True):
            self.ui['actions']['next'].invoke()
        self.assertEqual(self.ui['state']['step'], 1)
        self.ui['go'](4)
        self.assertIn('임시 사본으로 수집 1개', self.ui['summary']['대상 프로젝트'].get())
        self.assertEqual(len(self.ui['tree'].get_children()), 2)

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

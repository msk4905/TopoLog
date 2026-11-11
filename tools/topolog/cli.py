import argparse
import sys
import json
from pathlib import Path
import subprocess
import time
import os
from . import settings
from .engine import Runner, discover, draft, load_rules, save
from .transport import API
from . import backend


from .naming import new_output, result_folders


def connect(server=None, executable=None, progress=None, cancel=None):
    progress = progress or (lambda message: None)
    if cancel and cancel.is_set():
        raise InterruptedError('Connection cancelled')
    api = API(server)
    try:
        api.call('/v2/version')
        return api
    except Exception as exc:
        from urllib.error import HTTPError
        if isinstance(exc, HTTPError):
            raise ValueError(backend.failure(api, exc)) from exc
        first_error = exc
    addresses, candidates = backend.discover()
    if api.host not in addresses:
        raise ValueError(backend.failure(api, first_error) + ' This address was not verified as local; a remote VM must be started separately.')
    selected = Path(executable).resolve(strict=True) if executable else (candidates[0] if candidates else None)
    if not selected:
        raise ValueError(backend.failure(api, first_error) + ' Local backend executable not found; specify --server-executable.')
    progress('Starting local GNS3 backend: ' + str(selected))
    # The GNS3 GUI always starts its Desktop server with --local. Without it the
    # server reports local=false and rejects project image paths outside the
    # images folder, and the GUI later reuses this process instead of replacing it.
    process = subprocess.Popen([str(selected), '--local'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               env=backend.launch_environment(selected),
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if cancel and cancel.is_set():
            raise InterruptedError('Connection cancelled')
        try:
            api.call('/v2/version')
            return api
        except Exception as exc:
            from urllib.error import HTTPError
            if isinstance(exc, HTTPError):
                raise ValueError(backend.failure(api, exc)) from exc
            if process.poll() is not None:
                raise ValueError('GNS3 backend exited during startup. Check the GNS3 server log and configured host/port.') from exc
            time.sleep(.5)
    raise ValueError('Local GNS3 backend startup timed out at ' + api.base)


def main(argv=None):
    parser = argparse.ArgumentParser(description='TopoLog folder collection and explicit verification')
    parser.add_argument('folder', nargs='?', help='Project folder; defaults to saved/GNS3 path')
    parser.add_argument('--output-dir', help='Parent folder for a new run result (never overwrite an existing run)')
    parser.add_argument('--rules', type=Path)
    parser.add_argument('--project',action='append',help='Project UUID to collect; may repeat')
    parser.add_argument('--retry-from',type=Path,help='Fresh collection of incomplete projects from an earlier result')
    parser.add_argument('--share-copy',type=Path,help='Generate a redacted review copy; images require manual review')
    parser.add_argument('--server')
    parser.add_argument('--server-executable', type=Path)
    parser.add_argument('--prepare', action='store_true', help='Scan only and generate editable criteria; do not contact GNS3')
    parser.add_argument('--remember', action='store_true', help='Remember selected paths for future runs')
    parser.add_argument('--gui', action='store_true')
    parser.add_argument('--no-enable', action='store_true', help='Do not attempt privileged EXEC')
    parser.add_argument('--no-native-screenshot',action='store_true',help='Skip GNS3 GUI native image export')
    parser.add_argument('--no-annotate',action='store_true',help='Do not add subnet/cost captions to a copy of the GNS3 picture')
    parser.add_argument('--rebuild-annotations',type=Path,help='Redraw annotated picture copies and reports of a result folder from saved titles; no GNS3 access')
    parser.add_argument('--no-start-gns3', action='store_true', help='Do not launch the GNS3 program when it is not running')
    parser.add_argument('--enable-password-env', default='TOPOLOG_ENABLE_PASSWORD', help='Read enable password from this environment variable; never from command text')
    parser.add_argument('--refresh-report', nargs='?', const='latest', help='Refresh an existing report and ZIP without collecting')
    parser.add_argument('--reassess', type=Path, help='Evaluate an existing batch result with --rules; no GNS3 access')
    parser.add_argument('--console-workers',type=int,choices=(1,2,3,4),default=3,help='Concurrent consoles within one project (default: 3)')
    parser.add_argument('--detail-scope',choices=('representative','all'),help='Word/document detail: representative (up to 6, by detected feature kinds) or all projects')
    parser.add_argument('--settle-seconds', type=int, default=10)
    parser.add_argument('--ready-timeout', type=int, default=120)
    parser.add_argument('--compute-timeout', type=int, default=300, help='Wait for required GNS3 compute/VM connections')
    args = parser.parse_args(argv)
    from . import runtime
    print('TopoLog ' + runtime.VERSION + ' · structured-html-v1')
    from . import motw
    unblocked = motw.describe(motw.unblock())
    if unblocked:
        print(unblocked)
    if args.gui:
        from .gui import launch
        launch()
        return 0
    if not 0 <= args.settle_seconds <= 300 or not 1 <= args.ready_timeout <= 1800 or not 1 <= args.compute_timeout <= 1800:
        parser.error('settle-seconds: 0..300; ready-timeout and compute-timeout: 1..1800')
    try:
        if args.project and args.retry_from:raise ValueError('--project와 --retry-from은 함께 사용할 수 없습니다.')
        defaults = settings.load()
        if args.share_copy:
            from .sharing import create
            folder=new_output(args.output_dir or defaults['output_directory'],'공개검토')
            create(args.share_copy,folder)
            print('공개 검토용 사본:',folder/'index.html')
            return 0
        if args.rebuild_annotations:
            from . import annotate
            print('Report refreshed:', annotate.rebuild(args.rebuild_annotations, progress=print))
            return 0
        if args.refresh_report:
            from .viewer import latest_report, open_report
            from .presentation import refresh_report
            from .resultdir import is_result
            if args.refresh_report == 'all':
                folders=sorted(p for p in result_folders(defaults['output_directory']) if p.is_dir() and is_result(p))
                if not folders:raise ValueError('저장된 결과 보고서를 찾지 못했습니다.')
                for folder in folders:
                    print('Report refreshed:',refresh_report(folder,args.detail_scope))
                open_report(folders[-1]/'index.html')
                return 0
            folder = latest_report(defaults['output_directory']) if args.refresh_report=='latest' else Path(args.refresh_report)
            if folder is None:raise ValueError('저장된 결과 보고서를 찾지 못했습니다.')
            report = refresh_report(folder,args.detail_scope)
            print('Report refreshed:', report)
            open_report(report)
            return 0
        if args.reassess:
            from .criteria import reassess
            output = new_output(args.output_dir or defaults['output_directory'],'재검토')
            result = reassess(args.reassess, output, args.rules)
            print('보고서:', output / 'index.html')
            print('판정:', {'passed':'기준 충족','failed':'기준 불일치','inconclusive':'확인 필요'}[result['verdict']])
            return 0 if result['verdict'] == 'passed' else 2
        root, parent = settings.validate(args.folder or defaults['project_directory'], args.output_dir or defaults['output_directory'])
        # Discover/validate before server startup or changing any project state.
        _, entries = discover(root)
        from .selection import retry_ids,select
        ids=set(args.project) if args.project else (retry_ids(args.retry_from) if args.retry_from else None)
        entries=select(entries,ids)
        rules = load_rules(args.rules)
        if args.remember:
            settings.save(root, parent)
        output = new_output(parent)
        if args.prepare:
            output.mkdir()
            save(output / 'inventory.json', {'projects': entries})
            save(output / 'criteria.json', draft(root))
            print('Criteria draft:', output / 'criteria.json')
            return 0
        from .progress import wrap
        notify = wrap(print)
        if not args.server:
            from . import preflight
            preflight.prepare(API, not args.no_start_gns3 and defaults.get('auto_start_gns3', True), progress=notify,
                              needs_vm=preflight.projects_need_vm(root, entries))
        api = connect(args.server, args.server_executable, progress=notify)
        from .server_config import prepare
        prepare(api, root, entries, args.server_executable, progress=notify)
        def ask_password(name):
            import getpass
            if not sys.stdin.isatty():return None
            return getpass.getpass(name+' enable 암호 (취소하려면 Enter): ') or None
        result = Runner(api, output, rules, console_workers=args.console_workers,settle_seconds=args.settle_seconds,
                        ready_timeout=args.ready_timeout, progress=notify, compute_timeout=args.compute_timeout,
                        enable=not args.no_enable,enable_password=os.environ.get(args.enable_password_env),
                        password_provider=ask_password,capture=not args.no_native_screenshot,project_ids=ids,retry_source=args.retry_from,
                        detail_scope=args.detail_scope or defaults.get('detail_scope','representative'),
                        annotate=not args.no_annotate and defaults.get('annotate',True)).run(root)
        print('보고서:', output / 'index.html')
        print('압축 파일:', str(output) + '.zip')
        print('판정:', {'passed':'기준 충족','failed':'기준 불일치','inconclusive':'확인 필요'}[result['verdict']])
        return 0 if result['verdict'] == 'passed' else 2
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(1, 'TopoLog: ' + str(exc) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())

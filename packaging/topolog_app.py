"""Windows program entry: setup pages, then the run screen."""
import multiprocessing
import sys


def main():
    multiprocessing.freeze_support()
    if len(sys.argv) == 3 and sys.argv[1] == '--check-server':
        # Read-only connection probe usable even in a windowed frozen build.
        import json
        import os
        from pathlib import Path
        from topolog.transport import API
        record = {'frozen': bool(getattr(sys, 'frozen', False)),
                  'config_exists': (Path(os.environ.get('APPDATA', '')) / 'GNS3/2.2/gns3_server.ini').is_file()}
        try:
            api = API()
            record['server'] = api.base
            record['authentication_configured'] = bool(api.headers)
            record['version'] = api.call('/v2/version')
            record['status'] = 'connected'
        except Exception as exc:
            import traceback
            record.update(status='error', error_type=type(exc).__name__, error=str(exc),
                          traceback=traceback.format_exc())
        Path(sys.argv[2]).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        return 0 if record['status'] == 'connected' else 2
    from topolog.gui import launch
    launch()
    return 0


if __name__ == '__main__':
    sys.exit(main())

"""Cooperative pause between work units.

A pause request is honoured only where no console command, pager, password
prompt, file dialog or lock is in progress: before a project starts, before a
device session opens, and between the later per-project stages. Cancellation
is accepted immediately while paused.
"""
import threading


class PauseGate:
    def __init__(self):
        self._lock = threading.Lock()
        self._resume = threading.Event()
        self._resume.set()
        self.waiting = 0
        self.listeners = []

    @property
    def requested(self):
        return not self._resume.is_set()

    def request(self):
        self._resume.clear()

    def resume(self):
        self._resume.set()

    def wait(self, cancel=None, where=''):
        """Block at a safe boundary while a pause is requested; raise on cancel."""
        if self._resume.is_set():
            if cancel is not None and cancel.is_set():
                raise InterruptedError('Batch cancelled')
            return False
        with self._lock:
            self.waiting += 1
            first = self.waiting == 1
        if first:
            for listener in list(self.listeners):
                listener(True, where)
        try:
            while not self._resume.wait(.1):
                if cancel is not None and cancel.is_set():
                    raise InterruptedError('Batch cancelled while paused')
        finally:
            with self._lock:
                self.waiting -= 1
                last = self.waiting == 0
            if last:
                for listener in list(self.listeners):
                    listener(False, where)
        if cancel is not None and cancel.is_set():
            raise InterruptedError('Batch cancelled')
        return True

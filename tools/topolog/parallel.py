"""Bounded independent console work; drain sessions before lifecycle cleanup."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import threading


class LockedAPI:
    def __init__(self,api):
        self.api=api;self.lock=threading.RLock()
    def __getattr__(self,name):return getattr(self.api,name)
    def call(self,*args,**kwargs):
        with self.lock:return self.api.call(*args,**kwargs)
    def raw(self,*args,**kwargs):
        with self.lock:return self.api.raw(*args,**kwargs)


def completed(items,worker,workers=2,cancel=None):
    """Caller must close the iterator when leaving early (contextlib.closing)."""
    iterator=iter(items);pool=ThreadPoolExecutor(max_workers=max(1,min(2,int(workers))))
    pending=set();finished=False
    def submit():
        if cancel and cancel.is_set():raise InterruptedError('Collection cancelled')
        try:item=next(iterator)
        except StopIteration:return
        pending.add(pool.submit(worker,item))
    try:
        for _ in range(max(1,min(2,int(workers)))):submit()
        while pending:
            if cancel and cancel.is_set():raise InterruptedError('Collection cancelled')
            ready,_=wait(pending,timeout=.1,return_when=FIRST_COMPLETED)
            for future in ready:
                pending.remove(future)
                yield future.result()
                submit()
        finished=True
    finally:
        if not finished and cancel and hasattr(cancel,'set'):cancel.set()
        for future in pending:future.cancel()
        pool.shutdown(wait=True,cancel_futures=True)

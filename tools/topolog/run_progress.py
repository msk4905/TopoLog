"""Work-based progress; time estimates do not predict verification success."""
import time


def duration(seconds):
    seconds=max(0,int(seconds))
    if seconds>=3600:return '{}시간 {}분'.format(seconds//3600,(seconds%3600)//60)
    if seconds>=60:return '{}분 {}초'.format(seconds//60,seconds%60)
    return '{}초'.format(seconds)


class RunProgress:
    def __init__(self,clock=time.monotonic):
        self.clock=clock
        self.reset()

    def reset(self,label='서버·VM 준비'):
        self.started=self.clock();self.last=self.started
        self.fraction=0.;self.stage=label;self.active=True;self.paused=False
        self.busy_seconds=0.;self.eta=None;self.completed=False
        self.finished_elapsed=None
        self.input_paused=self.gate_paused=False
        # GNS3/VM start-up before the first project is a one-time cost, not a rate to extrapolate from.
        self.work_started_at=None;self.projects_done=0

    def _tick(self):
        now=self.clock()
        if self.active and not self.paused:self.busy_seconds+=max(0,now-self.last)
        self.last=now

    def update(self,event):
        self._tick()
        for key in ('project_name','project_index','project_total','task'):
            if key in event:setattr(self,key,event[key])
        self.fraction=max(self.fraction,min(.99,max(0,float(event.get('fraction',0)))))
        self.stage=event.get('stage',self.stage)
        # Two independent waits: a device password prompt and a user pause.
        if 'paused' in event:self.input_paused=bool(event['paused'])
        if 'gate_paused' in event:self.gate_paused=bool(event['gate_paused'])
        self.paused=getattr(self,'input_paused',False) or getattr(self,'gate_paused',False)
        self.pause_label='일시중지됨' if getattr(self,'gate_paused',False) else '사용자 입력 대기'
        if self.work_started_at is None and 'project_index' in event:self.work_started_at=self.busy_seconds
        if 'project_done' in event:self.projects_done+=1
        # Re-estimate at actual milestones, then count down between milestones. Steps inside one project
        # take very different times (ping retries, picture saving), so the first estimate waits for a
        # finished project, or for a quarter of the work when there is only one large project.
        worked=self.busy_seconds-(self.work_started_at or 0)
        ready=(self.projects_done>=1 or self.fraction>=.25) and worked>=5
        self.eta=worked*(1-self.fraction)/self.fraction if ready and self.fraction>0 else None
        self.eta_at=self.busy_seconds

    def finish(self,completed):
        self._tick();self.active=False;self.paused=self.input_paused=self.gate_paused=False;self.completed=completed
        self.finished_elapsed=self.clock()-self.started
        if completed:self.fraction=1.;self.eta=0
        self.stage='완료' if completed else '중지·오류'

    def snapshot(self):
        self._tick();elapsed=self.finished_elapsed if self.finished_elapsed is not None else self.clock()-self.started
        if not self.active:remaining='완료' if self.completed else '중지됨'
        elif self.paused:remaining=getattr(self,'pause_label','사용자 입력 대기')
        elif self.eta is None:remaining='측정 중(첫 프로젝트가 끝나면 계산)'
        else:
            left=self.eta-(self.busy_seconds-getattr(self,'eta_at',self.busy_seconds))
            remaining='약 '+duration(left) if left>0 else '다음 단계 확인 중'
        return {'percent':min(99,int(self.fraction*100)) if self.active else int(self.fraction*100),
                'text':'{} · 경과 {} · 예상 남은 시간 {}'.format(self.stage,duration(elapsed),remaining),
                'elapsed':duration(elapsed),'remaining':remaining,'stage':self.stage,
                'project_name':getattr(self,'project_name',''),'project_index':getattr(self,'project_index',None),
                'project_total':getattr(self,'project_total',None),'task':getattr(self,'task',self.stage)}

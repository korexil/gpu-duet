"""Conservative bounded tail gate: trims a quiet, non-periodic tail (a trailing breath)
after the last voiced audio of a sentence, holding at most ~0.8 s of pending audio."""
import numpy as np

class TailGate:
    def __init__(self, rate=24000):
        self.rate=rate
        self.hop=rate//50
        self.pending=[]
        self.remainder=np.empty(0,dtype=np.float32)
        self.peak=0.0
        self.started=False
        self.trimmed_samples=0
        self.max_pending_samples=0
        self.closed=False

    @staticmethod
    def rms(block):
        return float(np.sqrt(np.mean(block.astype(np.float64)**2))) if len(block) else 0.0

    def feed(self, wave):
        if self.closed: raise RuntimeError('tail gate already closed')
        data=np.concatenate([self.remainder,np.asarray(wave,dtype=np.float32)])
        whole=len(data)//self.hop*self.hop
        self.remainder=data[whole:].copy()
        output=[]
        for start in range(0,whole,self.hop):
            block=data[start:start+self.hop].copy()
            level=self.rms(block)
            self.peak=max(self.peak,level)
            strong=max(.006,self.peak*.04)
            if level>=strong:
                output.extend(self.pending)
                self.pending=[]
                self.started=True
                output.append(block)
            elif not self.started:
                output.append(block)  # No extra wait before first voice/PCM.
            else:
                self.pending.append(block)
                if len(self.pending)>40: output.append(self.pending.pop(0))
                self.max_pending_samples=max(self.max_pending_samples,sum(map(len,self.pending)))
        return np.concatenate(output) if output else np.empty(0,dtype=np.float32)

    def finish(self):
        if self.closed: raise RuntimeError('tail gate already closed')
        self.closed=True
        if len(self.remainder): self.pending.append(self.remainder)
        if not self.pending: return np.empty(0,dtype=np.float32)
        levels=np.array([self.rms(block) for block in self.pending])
        quiet=max(.0008,self.peak*.007)
        strong=max(.006,self.peak*.04)
        run=0
        boundary=None
        for index,level in enumerate(levels):
            if level<=quiet:
                run+=1
                continue
            if run>=7:
                tail=levels[index:]
                active=np.flatnonzero(tail>quiet*1.3)+index
                if len(active)>=2 and float(tail.max())<strong*.5:
                    periodic=[]
                    for i in active:
                        block=self.pending[i].astype(np.float64)
                        block-=block.mean()
                        ac=np.correlate(block,block,mode='full')[len(block)-1:]
                        valid=ac[self.rate//500:min(len(ac),self.rate//100)]
                        periodic.append(float(valid.max()/max(ac[0],1e-12)) if len(valid) else 1.0)
                    if max(periodic)<.4:
                        boundary=(index-run+7)*self.hop
                        break
            run=0
        output=np.concatenate(self.pending)
        if boundary is not None and 0<boundary<len(output):
            self.trimmed_samples=len(output)-boundary
            output=output[:boundary].copy()
            fade=min(self.hop,len(output))
            output[-fade:]*=np.linspace(1,0,fade,dtype=np.float32)
        return output

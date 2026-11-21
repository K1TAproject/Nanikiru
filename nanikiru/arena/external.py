"""Local JSON-lines subprocess transports. Not an OS security sandbox."""
import json
import queue
import subprocess
import threading

from ..analysis.output import debug_json
from ..contracts import Action


class JsonProcess:
    def __init__(self, command, cwd, timeout, stderr):
        self.timeout=timeout
        self.process=subprocess.Popen(command,cwd=cwd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=stderr,text=True,encoding='utf-8',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))

    def ask(self, payload):
        output=queue.Queue(maxsize=1)
        def exchange():
            try:
                self.process.stdin.write(json.dumps(payload,ensure_ascii=False,allow_nan=False)+'\n')
                self.process.stdin.flush()
                line=self.process.stdout.readline(1024*1024)
                if not line:
                    try:code=self.process.wait(timeout=1)
                    except subprocess.TimeoutExpired:code=None
                    detail='still running with stdout closed' if code is None else f'exit={code} (0x{code & 0xffffffff:08X})'
                    raise RuntimeError('External bot closed stdout before replying: '+detail)
                if not line.endswith('\n'):
                    raise ValueError('External bot oversized response' if len(line)>=1024*1024 else 'External bot truncated response')
                output.put(json.loads(line))
            except Exception as error:output.put(error)
        worker=threading.Thread(target=exchange,daemon=True);worker.start()
        try:reply=output.get(timeout=self.timeout)
        except queue.Empty:
            self.process.kill();self.process.wait();worker.join(timeout=2)
            raise TimeoutError('External bot response timed out') from None
        if isinstance(reply,Exception):raise reply
        return reply

    def close(self):
        if self.process.poll() is None:self.process.kill()
        self.process.wait()
        self.process.stdin.close();self.process.stdout.close()


class RequestBot(JsonProcess):
    """One full visible AnalysisRequest in, one Action out; version echoed."""
    def decide(self, turn, record):
        reply=self.ask(dict(protocol='nanikiru-bot-v1',request=json.loads(debug_json(turn.request))))
        if reply.get('state_version')!=turn.request.state_version:raise ValueError('External bot returned stale version')
        raw=reply['action'];raw['tile_ids']=tuple(raw.get('tile_ids',()))
        return Action(**raw)

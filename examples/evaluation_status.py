"""Inspect the live evaluator lock as well as its last saved progress."""
import argparse
import errno
import json
from pathlib import Path
import time

from nanikiru.arena.evaluation import read, run_lock


def status(folder):
    folder=Path(folder)
    if not (folder/'manifest.json').is_file():raise ValueError('Not an evaluation output directory')
    try:
        with run_lock(folder):
            result=read(folder/'status.json');active=False
    except OSError as error:
        if error.errno not in (errno.EACCES,errno.EAGAIN,errno.EDEADLK):raise
        result=read(folder/'status.json');active=True
    result['process_active']=active
    if not active and result['stage']=='running':
        result['stage']='interrupted_without_final_status'
        result['note']='No evaluator holds the run lock; rerun the identical command to resume.'
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory');parser.add_argument('--watch',action='store_true')
    args=parser.parse_args();previous=None
    try:
        while True:
            result=status(args.directory)
            if result!=previous:print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
            previous=result
            if not args.watch or not result['process_active']:break
            time.sleep(2)
    except KeyboardInterrupt:pass


if __name__=='__main__':main()

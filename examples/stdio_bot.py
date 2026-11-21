"""Reference nanikiru-bot-v1 process; demonstrates transport, not an external strength benchmark."""
from dataclasses import asdict
import json
import sys

from nanikiru.arena.session import inspect_turn
from nanikiru.contracts import request_from_dict


def main():
    for line in sys.stdin:
        payload=json.loads(line)
        if payload['protocol']!='nanikiru-bot-v1':raise ValueError('Unknown protocol')
        request=request_from_dict(payload['request'])
        action=inspect_turn(request).action
        if action is None:raise ValueError('No executable recommendation')
        print(json.dumps(dict(state_version=request.state_version,action=asdict(action))),flush=True)


if __name__=='__main__':main()

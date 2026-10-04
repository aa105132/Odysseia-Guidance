"""Isolated OpenSpiel worker; accepts only the sanitized snapshot on stdin."""
import json
from pathlib import Path
import sys


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[5]))
    if len(sys.argv) > 1:
        sys.path.insert(0, sys.argv[1])
    from importlib import import_module
    from importlib.metadata import version
    if version('open_spiel') != '1.6.15':
        raise ValueError('Unsupported OpenSpiel version')
    search = import_module('src.chat.features.games.blackjack-web.texas_search')
    raw = sys.stdin.buffer.read(131073)
    if len(raw) > 131072:
        raise ValueError('Oversized search request')
    request = json.loads(raw)
    result = search.solve(request['state'], request['simulations'], request['seed'])
    print(json.dumps(result['action']))


if __name__ == '__main__':
    main()

"""Create a consistent private SQLite backup while the local server is running."""
import argparse
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def backup(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if not source.is_file():
        raise ValueError('Source database does not exist')
    target.parent.mkdir(parents=True, exist_ok=True)
    # Never overwrite an existing backup or live database.
    with target.open('xb'):
        pass
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(target)) as dest:
            src.backup(dest)
            if dest.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Backup integrity check failed')
    return target


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',default=str(ROOT/'data'/'arian.sqlite'))
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    parser.add_argument('--out',default=str(ROOT/'backups'/f'arian-state-{stamp}.sqlite'))
    args=parser.parse_args()
    print('Private backup:',backup(args.source,args.out))
    print('Do not upload this account/trade database to a source-code repository.')


if __name__=='__main__':main()

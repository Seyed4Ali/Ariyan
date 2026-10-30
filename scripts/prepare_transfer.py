"""Build a code-only transfer ZIP from an explicit allowlist. No local account data."""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def prepare(target):
    target=Path(target).resolve()
    if target.exists():
        raise ValueError('Use a new archive filename; existing files are never overwritten')
    files={}
    for name in ('.gitignore','.gitattributes','server.py','backtest.py','config.json','requirements.txt',
                 'Start-Arian.cmd','Stop-Arian.cmd','Setup-Arian.cmd'):
        files[name]=(ROOT/name).read_bytes()
    files['README.md']=(ROOT/'README-PORTABLE.md').read_bytes()
    files['README-PORTABLE.md']=(ROOT/'README-PORTABLE.md').read_bytes()
    files['data/.gitkeep']=b''
    for folder,extensions in (('arian',{'.py'}),('web',{'.html','.css','.js'}),
                              ('scripts',{'.py','.ps1'}),('tests',{'.py'}),('mt5',{'.mq5'})):
        for path in (ROOT/folder).rglob('*'):
            if path.is_file() and not path.is_symlink() and '__pycache__' not in path.parts and path.suffix in extensions:
                files[path.relative_to(ROOT).as_posix()]=path.read_bytes()
    for name in ('docs/transfer-fa.md','docs/checklist-fa.md','docs/AGENT-HANDOFF.md'):
        files[name]=(ROOT/name).read_bytes()
    # Machine-specific paths and any unknown runtime fields are not shipped.
    config=json.loads(files['config.json'])
    config['terminal_path']=r'C:\Program Files\MetaTrader 5\terminal64.exe'
    for key in ('login','password','server','MT5_LOGIN','MT5_PASSWORD'):
        config.pop(key,None)
    files['config.json']=(json.dumps(config,indent=2,ensure_ascii=False)+'\n').encode('utf-8')
    manifest={name:hashlib.sha256(data).hexdigest() for name,data in sorted(files.items())}
    files['BUILD-MANIFEST.json']=json.dumps(manifest,indent=2).encode('utf-8')
    target.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(target,'x',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,data in sorted(files.items()):
            archive.writestr('Arian-Trader/'+name,data)
    return target,len(files)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    path,count=prepare(args.out)
    print(f'Code-only archive: {path}\nFiles: {count}\nPrivate database and reference chats excluded.')

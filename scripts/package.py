"""Package a runnable source distribution, excluding local data and secrets."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = ('backend', 'frontend/src', 'frontend/public', 'frontend/dist', 'scripts', 'docs', 'qa', 'licenses', 'assets')
FILES = ('README.md', 'requirements.txt', '.env.example', '.gitignore', '.dockerignore',
         'Dockerfile', 'compose.yaml', 'openapi.json', 'frontend/package.json',
         'frontend/package-lock.json', 'frontend/tsconfig.json', 'frontend/vite.config.ts',
         'frontend/index.html')


def main():
    output = ROOT / 'output' / 'aeza-renewals.zip'
    output.parent.mkdir(exist_ok=True)
    paths = [ROOT / name for name in FILES]
    for directory in DIRECTORIES:
        paths.extend((ROOT / directory).rglob('*'))
    selected = sorted({p for p in paths if p.is_file() and '__pycache__' not in p.parts
                       and '.pytest_cache' not in p.parts and p.suffix not in {'.pyc', '.pyo'}
                       and not p.name.endswith('.tsbuildinfo')})
    required = (ROOT / 'frontend/dist/index.html', ROOT / 'openapi.json', ROOT / 'frontend/package-lock.json')
    if any(path not in selected for path in required):
        raise SystemExit('Build frontend and export OpenAPI before packaging.')
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for path in selected:
            archive.write(path, Path('invoice-keeper') / path.relative_to(ROOT))
    with ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise SystemExit('Archive integrity check failed.')
        if any(Path(name).name == '.env' or '/data/' in name or 'local-access' in name for name in archive.namelist()):
            raise SystemExit('Unsafe local file in archive.')
    print(f'Created {output.name}: {len(selected)} files, {output.stat().st_size} bytes. Local secrets and data excluded.')


if __name__ == '__main__':
    main()

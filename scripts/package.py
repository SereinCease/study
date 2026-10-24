"""Create a shareable source bundle using an explicit allowlist (never local secrets)."""
from pathlib import Path
import zipfile
ROOT=Path(__file__).resolve().parents[1]
ALLOWED_DIRS=['src','docs','server/skills','runner','samples','scripts','tests','design/ink-starlight']
ALLOWED_FILES=['README.md','pytest.ini','package.json','package-lock.json','requirements.lock','requirements.in','.env.example','.gitignore','index.html','vite.config.js','server/requirements.txt','reports/verification.md','reports/unit-integration.xml','reports/sandbox.xml','reports/sandbox-extra.xml','reports/acceptance.json','reports/acceptance-observed.json','reports/semantic.json','reports/ui.json']
def main():
    output=ROOT.parent/'learnloop-source.zip'
    files=[ROOT/name for name in ALLOWED_FILES]
    files += [p for p in (ROOT/'reports').glob('*-v2.*') if p.is_file()]
    files += [p for p in (ROOT/'reports').glob('*-v3.*') if p.is_file()]
    files += [p for p in (ROOT/'reports/previews').glob('*.png')]
    files += list((ROOT/'server').glob('*.py'))
    for directory in ALLOWED_DIRS:
        files += [p for p in (ROOT/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.log')]
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        for path in sorted(set(files)):
            if path.exists():z.write(path,'learnloop/'+str(path.relative_to(ROOT)))
    print(output)
if __name__=='__main__':main()

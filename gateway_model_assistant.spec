# Reproducible portable Windows app; build environment is isolated from user tools.
from pathlib import Path

root = Path(SPECPATH)
a = Analysis(
    [str(root / 'app.py')], pathex=[str(root)],
    # Only runtime assets; retain design originals in source, not the release.
    binaries=[], datas=[(str(root / 'assets' / name), 'assets') for name in (
        'app.ico', 'app.png', 'target-trae.png', 'target-traework.png', 'target-workbuddy.png')],
    hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['PIL', 'numpy', 'pytest', 'pip', 'setuptools'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name='网关模型助手', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False,
    icon=str(root / 'assets' / 'app.ico'),
    version=str(root / 'windows_version_info.txt'),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='网关模型助手')

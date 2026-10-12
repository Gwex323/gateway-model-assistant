$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$python = Join-Path $root '.build-venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Create .build-venv and install requirements-build.txt first.'
}
Push-Location -LiteralPath $root
try {
    & $python -m PyInstaller --noconfirm --workpath build/windows --distpath dist/windows gateway_model_assistant.spec
    if ($LASTEXITCODE -ne 0) { throw 'Application build failed.' }
} finally {
    Pop-Location
}

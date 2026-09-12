$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $PSScriptRoot '.venv-camera\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Create .venv-camera with standard Python; see README.md.'
}
& $pythonPath (Join-Path $PSScriptRoot 'list_cameras.py') @args
exit $LASTEXITCODE

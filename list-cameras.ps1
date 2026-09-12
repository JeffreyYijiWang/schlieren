$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Install the Python environment first; see README.md.'
}
& $pythonPath (Join-Path $PSScriptRoot 'list_cameras.py') @args
exit $LASTEXITCODE

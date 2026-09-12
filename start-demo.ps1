$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $PSScriptRoot '.venv-camera\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Create .venv-camera with standard Python (not Microsoft Store Python); see README.md.'
}
& $pythonPath (Join-Path $PSScriptRoot 'serve_demo.py') @args
exit $LASTEXITCODE

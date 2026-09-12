$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Install the Python environment first; see README.md.'
}
& $pythonPath (Join-Path $PSScriptRoot 'serve_demo.py') @args
exit $LASTEXITCODE

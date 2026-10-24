function Get-EspPython {
    param([string]$Python, [string]$EspRoot = (Join-Path $env:USERPROFILE 'esp'))
    if ($Python) { return $Python }
    if ($env:IDF_PYTHON_ENV_PATH) {
        $candidate = Join-Path $env:IDF_PYTHON_ENV_PATH 'Scripts\python.exe'
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    $candidate = Join-Path $EspRoot 'tools\python_env\idf5.3_py3.12_env\Scripts\python.exe'
    if (Test-Path -LiteralPath $candidate) { return $candidate }
    return (Get-Command python -ErrorAction Stop).Source
}

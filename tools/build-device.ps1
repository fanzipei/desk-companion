param([switch]$Flash, [string]$Port = 'COM6', [string]$EspRoot = (Join-Path $env:USERPROFILE 'esp'))
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
if (!$env:IDF_PATH) { $env:IDF_PATH = Join-Path $EspRoot 'esp-idf' }
if (!$env:IDF_TOOLS_PATH) { $env:IDF_TOOLS_PATH = Join-Path $EspRoot 'tools' }
if (!$env:IDF_PYTHON_ENV_PATH) { $env:IDF_PYTHON_ENV_PATH = Join-Path $EspRoot 'tools\python_env\idf5.3_py3.12_env' }
$env:IDF_PYTHON_CHECK_CONSTRAINTS = 'no'
$env:IDF_SKIP_CHECK_SUBMODULES = '1'
$env:PYTHONUTF8 = '1'
$env:Path = "$env:IDF_PYTHON_ENV_PATH\Scripts;$EspRoot\tools\tools\cmake\3.30.2\bin;$EspRoot\tools\tools\ninja\1.12.1;$EspRoot\tools\tools\xtensa-esp-elf\esp-13.2.0_20240530\xtensa-esp-elf\bin;$env:Path"
$idfPython = Join-Path $env:IDF_PYTHON_ENV_PATH 'Scripts\python.exe'
$scratchBuild = Join-Path $env:TEMP 'desk-companion-idf-build'
$distribution = Join-Path $projectRoot 'device\dist'
New-Item -ItemType Directory -Force -Path $distribution | Out-Null
Push-Location (Join-Path $projectRoot 'device')
try {
    & $idfPython "$env:IDF_PATH\tools\idf.py" -B $scratchBuild build
    if ($LASTEXITCODE -ne 0) { throw 'Firmware build failed.' }
    Copy-Item -LiteralPath (Join-Path $scratchBuild 'desk_companion.bin') -Destination $distribution
    Copy-Item -LiteralPath (Join-Path $scratchBuild 'bootloader\bootloader.bin') -Destination $distribution
    Copy-Item -LiteralPath (Join-Path $scratchBuild 'partition_table\partition-table.bin') -Destination $distribution
    if ($Flash) {
        & (Join-Path $PSScriptRoot 'flash-companion.ps1') -Port $Port
    }
} finally { Pop-Location }

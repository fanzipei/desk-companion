param([string]$Port = 'COM6', [switch]$RestoreOriginal, [string]$Python,
      [string]$EspRoot = (Join-Path $env:USERPROFILE 'esp'))
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'esp-python.ps1')
$pythonExe = Get-EspPython -Python $Python -EspRoot $EspRoot
$projectRoot = Split-Path $PSScriptRoot -Parent
$backup = Join-Path $projectRoot 'backups\original-full-16MB.bin'
$manifest = Get-Content -LiteralPath (Join-Path $projectRoot 'backups\original-manifest.json') -Raw | ConvertFrom-Json
if ((Get-Item -LiteralPath $backup).Length -ne 16777216 -or
    (Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash -ne $manifest.sha256) {
    throw 'The original full flash backup is missing or damaged.'
}
if ($RestoreOriginal) {
    $bytes = [System.IO.File]::ReadAllBytes($backup)
    $appRegion = [byte[]]::new(0x200000)
    $storageRegion = [byte[]]::new(0x80000)
    [Array]::Copy($bytes, 0, $appRegion, 0, $appRegion.Length)
    [Array]::Copy($bytes, 0x900000, $storageRegion, 0, $storageRegion.Length)
    $appFile = Join-Path $projectRoot 'backups\original-app-region.bin'
    $storageFile = Join-Path $projectRoot 'backups\original-storage-region.bin'
    [System.IO.File]::WriteAllBytes($appFile, $appRegion)
    [System.IO.File]::WriteAllBytes($storageFile, $storageRegion)
    & $pythonExe -m esptool --chip esp32s3 --port $Port --baud 921600 write_flash 0x0 $appFile 0x900000 $storageFile
} else {
    $distribution = Join-Path $projectRoot 'device\dist'
    if ((Get-Item -LiteralPath (Join-Path $distribution 'desk_companion.bin')).Length + 0x10000 -gt 0x200000) {
        throw 'The app exceeds the region covered by the rollback command.'
    }
    & $pythonExe -m esptool --chip esp32s3 --port $Port --baud 921600 write_flash --flash_mode dio --flash_size 16MB --flash_freq 80m 0x0 (Join-Path $distribution 'bootloader.bin') 0x8000 (Join-Path $distribution 'partition-table.bin') 0x10000 (Join-Path $distribution 'desk_companion.bin')
}
if ($LASTEXITCODE -ne 0) { throw 'Flashing failed.' }

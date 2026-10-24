# Copyright 2026 fanzipei
# SPDX-License-Identifier: Apache-2.0
param([string]$Port = 'COM6', [string]$Python, [string]$EspRoot = (Join-Path $env:USERPROFILE 'esp'))
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'esp-python.ps1')
$pythonExe = Get-EspPython -Python $Python -EspRoot $EspRoot
$projectRoot = Split-Path $PSScriptRoot -Parent
$directory = Join-Path $projectRoot 'backups'
New-Item -ItemType Directory -Force -Path $directory | Out-Null
$backup = Join-Path $directory 'original-full-16MB.bin'
$manifest = Join-Path $directory 'original-manifest.json'
if ((Test-Path -LiteralPath $backup) -or (Test-Path -LiteralPath $manifest)) {
    if (!(Test-Path -LiteralPath $backup) -or !(Test-Path -LiteralPath $manifest)) {
        throw 'An incomplete backup already exists. Preserve it and resolve it before continuing.'
    }
    $record = Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json
    if ((Get-Item -LiteralPath $backup).Length -ne 16777216 -or
        (Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash -ne $record.sha256) {
        throw 'Existing backup checksum does not match. It will not be overwritten.'
    }
    Write-Output 'Existing full flash backup verified; no backup overwritten.'
    return
}
$temporary = Join-Path $directory 'original-full-16MB.pending.bin'
& $pythonExe -m esptool --chip esp32s3 --port $Port --baud 921600 read_flash 0x0 0x1000000 $temporary
if ($LASTEXITCODE -ne 0 -or (Get-Item -LiteralPath $temporary).Length -ne 16777216) {
    throw 'Backup failed. No firmware has been written.'
}
Move-Item -LiteralPath $temporary -Destination $backup
@{ chip='esp32s3'; bytes=16777216; sha256=(Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash;
   created_utc=(Get-Date).ToUniversalTime().ToString('o') } |
    ConvertTo-Json | Set-Content -LiteralPath $manifest -Encoding utf8
Write-Output 'Full flash backup and SHA256 manifest saved in backups/.'

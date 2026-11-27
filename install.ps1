# hsu-reserve installer (Windows PowerShell):
#   irm https://raw.githubusercontent.com/wantkdd/hsu-reserve/master/install.ps1 | iex
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
$repo = "https://github.com/wantkdd/hsu-reserve/archive/refs/heads/master.zip"

Write-Host "=== Hansung reservation (hsu-reserve) installer ==="

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "[1/3] Installing uv ..."
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "uv installation failed" }
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
} else {
    Write-Host "[1/3] uv found"
}

Write-Host "[2/3] Installing program ..."
uv tool install --refresh --force --quiet $repo
if ($LASTEXITCODE -ne 0) { throw "uv tool install failed" }

Write-Host "[3/3] Setup"
$bin = (uv tool dir --bin).Trim()
if ($LASTEXITCODE -ne 0) { throw "uv tool dir failed" }
& "$bin\hsu-reserve-mcp.exe" setup
if ($LASTEXITCODE -ne 0) { throw "hsu-reserve setup failed (exit $LASTEXITCODE)" }

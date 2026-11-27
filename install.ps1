# hsu-reserve installer (Windows PowerShell):
#   irm https://raw.githubusercontent.com/wantkdd/hsu-reserve/master/install.ps1 | iex
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
$repo = "git+https://github.com/wantkdd/hsu-reserve"

Write-Host "=== Hansung reservation (hsu-reserve) installer ==="

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "[1/3] Installing uv ..."
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" | Out-Null
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
} else {
    Write-Host "[1/3] uv found"
}

Write-Host "[2/3] Installing program ..."
uv tool install --force --quiet $repo
if ($LASTEXITCODE -ne 0) { throw "uv tool install failed" }

Write-Host "[3/3] Setup"
$bin = (uv tool dir --bin).Trim()
& "$bin\hsu-reserve-mcp.exe" setup

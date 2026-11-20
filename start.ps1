# Handover: one-command start for Windows PowerShell.
#   .\start.ps1            build what's missing, then serve on http://localhost:8000
#   .\start.ps1 -Dev       API with auto-reload + Vite dev server on http://localhost:5173
param([switch]$Dev)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "Handover needs uv (Python package manager): https://docs.astral.sh/uv/getting-started/installation/" -ForegroundColor Yellow
  exit 1
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
  Write-Host "Handover needs Node.js 20+ for the dashboard: https://nodejs.org" -ForegroundColor Yellow
  exit 1
}
if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host "Created .env from .env.example" }

uv sync
if (-not (Test-Path frontend/node_modules)) { Push-Location frontend; npm install; Pop-Location }
if (-not (Test-Path sample_data/demo_drive)) { uv run python scripts/generate_corpus.py }

if ($Dev) {
  Start-Process -NoNewWindow -FilePath "npm" -ArgumentList "run", "dev" -WorkingDirectory "frontend"
  Write-Host "`nHandover (dev): dashboard http://localhost:5173 · API http://localhost:8000`n" -ForegroundColor Green
  uv run uvicorn handover.main:app --port 8000 --reload --reload-dir handover
} else {
  if (-not (Test-Path frontend/dist/index.html)) { Push-Location frontend; npm run build; Pop-Location }
  Write-Host "`nHandover is running at http://localhost:8000`n" -ForegroundColor Green
  uv run uvicorn handover.main:app --port 8000
}

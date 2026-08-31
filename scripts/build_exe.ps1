[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$iconPath = Join-Path $projectRoot "src\forgeflow\resources\forgeflow.ico"
$outputPath = Join-Path $projectRoot "dist\ForgeFlow.exe"
$stagingPath = Join-Path $projectRoot "build\release\ForgeFlow.exe"

Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath $iconPath -PathType Leaf)) {
        python .\scripts\generate_app_icon.py
    }

    python -c "import PyInstaller" 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller is not installed. Run: python -m pip install -e .[build]"
    }

    python -m PyInstaller --noconfirm --clean --distpath .\build\release .\ForgeFlow.spec
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $stagingPath -PathType Leaf)) {
        throw "Failed to build ForgeFlow.exe."
    }

    $publishedPath = $outputPath
    try {
        Copy-Item -LiteralPath $stagingPath -Destination $outputPath -Force -ErrorAction Stop
    }
    catch {
        $publishedPath = Join-Path $projectRoot "dist\ForgeFlow-update.exe"
        Copy-Item -LiteralPath $stagingPath -Destination $publishedPath -Force -ErrorAction Stop
        Write-Warning "ForgeFlow.exe is currently running. The new build was published as ForgeFlow-update.exe."
    }

    $size = [Math]::Round((Get-Item -LiteralPath $publishedPath).Length / 1MB, 1)
    Write-Host ""
    Write-Host "Build complete: $publishedPath ($size MB)" -ForegroundColor Green
    Write-Host "You can now launch the executable by double-clicking it."
}
finally {
    Pop-Location
}

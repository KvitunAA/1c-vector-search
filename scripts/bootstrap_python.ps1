# Локальный Python 3.12 для проекта (kuzu имеет wheel только для 3.10–3.13).
# Python 3.14 и неполная установка 3.12 в системе не подходят.
param(
    [string]$ToolsDir = (Join-Path (Split-Path $PSScriptRoot -Parent) ".tools\python312")
)

$ErrorActionPreference = "Stop"
$pythonExe = Join-Path $ToolsDir "python.exe"
$zipUrl = "https://www.python.org/ftp/python/3.12.8/python-3.12.8-embed-amd64.zip"
$zipPath = Join-Path $env:TEMP "python-3.12.8-embed-amd64.zip"
$pthPath = Join-Path $ToolsDir "python312._pth"
$sitePackages = Join-Path $ToolsDir "Lib\site-packages"
$projectRoot = Split-Path $PSScriptRoot -Parent

function Ensure-EmbedPython {
    if (Test-Path $pythonExe) {
        Write-Host "Python уже установлен: $pythonExe"
        return
    }

    Write-Host "Скачивание Python 3.12.8 embed..."
    New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath
    Expand-Archive -Path $zipPath -DestinationPath $ToolsDir -Force

    @(
        "python312.zip"
        "."
        "Lib\site-packages"
        "import site"
    ) | Set-Content -Path $pthPath -Encoding ASCII

    New-Item -ItemType Directory -Force -Path $sitePackages | Out-Null

    $getPip = Join-Path $env:TEMP "get-pip.py"
    Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPip
    & $pythonExe $getPip --no-warn-script-location
}

Ensure-EmbedPython

Write-Host "Установка зависимостей..."
& $pythonExe -m pip install -r (Join-Path $projectRoot "requirements.txt") --no-warn-script-location

$localEnv = Join-Path $projectRoot "local.env"
$localContent = @"
# Пути для текущей машины (не коммитить)
VECTOR_PYTHON_PATH=$pythonExe
"@
Set-Content -Path $localEnv -Value $localContent -Encoding UTF8

Write-Host ""
Write-Host "Готово."
Write-Host "Python:   $pythonExe"
Write-Host "local.env обновлён (VECTOR_PYTHON_PATH)."
Write-Host "Тесты:    run_tests.cmd"

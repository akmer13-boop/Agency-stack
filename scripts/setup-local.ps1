[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

[Console]::InputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

Write-Host "=== Agency Stack: локальная настройка Windows ===" -ForegroundColor Cyan

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3.12 --version
    if ($LASTEXITCODE -ne 0) {
        throw "Python 3.12 не найден через py launcher. Установите Python 3.12 и повторите запуск."
    }

    if (-not (Test-Path ".venv")) {
        Write-Host "Создаю виртуальное окружение .venv..."
        & py -3.12 -m venv .venv
    }
}
elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $Version = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ([version]$Version -lt [version]"3.12") {
        throw "Нужен Python 3.12 или новее. Найдена версия $Version."
    }

    if (-not (Test-Path ".venv")) {
        Write-Host "Создаю виртуальное окружение .venv..."
        & python -m venv .venv
    }
}
else {
    throw "Python не найден. Установите Python 3.12 и добавьте его в PATH."
}

$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

Write-Host "Обновляю pip..."
& $VenvPython -m pip install --upgrade pip

Write-Host "Устанавливаю проект и dev-зависимости..."
& $VenvPython -m pip install -e ".[dev]"

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Создан файл .env из шаблона. Заполните секреты перед запуском." -ForegroundColor Yellow
}
else {
    Write-Host "Файл .env уже существует — не изменяю его."
}

Write-Host ""
Write-Host "=== НАСТРОЙКА ЗАВЕРШЕНА ===" -ForegroundColor Green
Write-Host "Рабочая ветка: $(git branch --show-current)"
Write-Host ""
Write-Host "Следующие команды:"
Write-Host "  notepad .env"
Write-Host "  .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"
Write-Host ""
Write-Host "После запуска откройте: http://127.0.0.1:8000/docs"

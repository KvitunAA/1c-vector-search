# Добавляет публичный ключ коллеги к общей учётной записи MCP на сервере.
# Запускать НА СЕРВЕРЕ SERVER_HOST в PowerShell ОТ ИМЕНИ АДМИНИСТРАТОРА.
#
# Пример:
#   powershell -ExecutionPolicy Bypass -File add_mcp_user_key.ps1 `
#       -PublicKey "ssh-ed25519 AAAAC3Nz... ivanov@NOTE-IVANOV" -Account mcp-user
#
# Ключ каждого человека - отдельная строка в authorized_keys. Чтобы отозвать доступ
# у одного, удалите его строку: остальные продолжат работать.

param(
    [Parameter(Mandatory = $true)][string]$PublicKey,
    [string]$Account = 'mcp-user'
)

$ErrorActionPreference = 'Stop'

# --- 1. Проверка формата ключа -------------------------------------------------
$PublicKey = $PublicKey.Trim()
if ($PublicKey -match 'PRIVATE KEY') {
    throw "ЭТО ПРИВАТНЫЙ КЛЮЧ. Нужен файл с расширением .pub. Приватный ключ никогда не покидает машину владельца - если он всё же был отправлен, его нужно перевыпустить."
}
if ($PublicKey -notmatch '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp\d+)\s+\S+') {
    throw "Это не похоже на публичный ключ. Ожидается строка вида 'ssh-ed25519 AAAAC3Nz... комментарий'. Проверьте, что взяли содержимое файла .pub, а не приватного ключа."
}

# --- 2. Учётная запись и её профиль --------------------------------------------
try {
    $sid = (New-Object System.Security.Principal.NTAccount($Account)).Translate(
        [System.Security.Principal.SecurityIdentifier]).Value
} catch {
    throw "Учётная запись '$Account' не найдена. Создайте её (см. SCALING.md, раздел 'Учётная запись на сервере')."
}

# Путь к профилю берём из реестра по SID, а не собираем как C:\Users\<имя>.
# Для доменной учётки $Account приходит как 'DOMAIN\user' (обратный слэш сломал бы путь),
# а каталог профиля может называться 'user.DOMAIN' при конфликте имён.
$profileKey = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\$sid"
$profileDir = $null
if (Test-Path $profileKey) {
    $profileDir = (Get-ItemProperty -Path $profileKey -Name ProfileImagePath -ErrorAction SilentlyContinue).ProfileImagePath
    if ($profileDir) { $profileDir = [Environment]::ExpandEnvironmentVariables($profileDir) }
}

if (-not $profileDir -or -not (Test-Path $profileDir)) {
    $sam = ($Account -split '\\')[-1]
    throw @"
Профиль учётной записи '$Account' не найден - она ещё ни разу не входила в систему.
Windows создаёт профиль только при первом входе, а до этого положить authorized_keys некуда.
Выполните однократный вход под ней, например:
    runas /user:$sam cmd
затем закройте окно и запустите этот скрипт снова.
"@
}

Write-Host "Профиль: $profileDir"

$sshDir = Join-Path $profileDir '.ssh'
$file   = Join-Path $sshDir 'authorized_keys'

if (-not (Test-Path $sshDir)) { New-Item -ItemType Directory -Path $sshDir | Out-Null }
if (-not (Test-Path $file))   { New-Item -ItemType File -Path $file | Out-Null }

# --- 3. Добавление ключа (идемпотентно) ----------------------------------------
# Сверяем по телу ключа, а не по всей строке: комментарий может отличаться.
$keyBody = ($PublicKey -split '\s+')[1]
$already = Select-String -Path $file -SimpleMatch $keyBody -Quiet

if ($already) {
    Write-Host "Ключ уже прописан в $file - ничего не меняю."
} else {
    # Если в конце файла нет перевода строки, Add-Content приклеит ключ к последней
    # строке и сломает её. Дописываем перевод строки заранее.
    $raw = [IO.File]::ReadAllText($file)
    if ($raw.Length -gt 0 -and -not $raw.EndsWith("`n")) {
        [IO.File]::AppendAllText($file, "`n")
    }
    # -Encoding ascii обязателен: UTF-16/BOM ломает разбор authorized_keys
    Add-Content -Path $file -Value $PublicKey -Encoding ascii
    Write-Host "Ключ добавлен в $file"
}

# --- 4. Права ------------------------------------------------------------------
# sshd игнорирует authorized_keys, если файл доступен на запись посторонним.
# Имена групп задаём через SID: на локализованной Windows 'Administrators'/'SYSTEM'
# не разрешаются, а /inheritance:r при этом уже сносит унаследованные права.
$out = icacls $file /inheritance:r /grant "*${sid}:F" /grant '*S-1-5-18:F' /grant '*S-1-5-32-544:F' 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Warning "icacls завершился с кодом $LASTEXITCODE - права НЕ выставлены, sshd проигнорирует ключ:"
    $out | ForEach-Object { Write-Warning "  $_" }
} else {
    Write-Host "Права на ${file}: $Account + SYSTEM + Администраторы"
}

# --- 5. Итог -------------------------------------------------------------------
$keys = @(Get-Content $file | Where-Object { $_.Trim() -ne '' -and -not $_.StartsWith('#') })
Write-Host "`nВсего ключей у '$Account': $($keys.Count)"
$keys | ForEach-Object {
    $parts = $_ -split '\s+'
    $comment = if ($parts.Count -ge 3) { $parts[2..($parts.Count - 1)] -join ' ' } else { '(без комментария)' }
    Write-Host "  - $comment"
}

Write-Host "`nПередайте коллеге строку подключения:"
Write-Host "  $Account@SERVER_HOST"

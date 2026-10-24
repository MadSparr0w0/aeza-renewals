param(
    [int]$Port = 8000,
    [switch]$NoWorker,
    [switch]$NoBot
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Сначала создайте .venv и установите requirements.txt по инструкции README.md.'
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot '.env'))) {
    throw 'Сначала создайте .env командой python scripts/init_env.py по инструкции README.md.'
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'frontend\dist\index.html'))) {
    throw 'Сначала соберите интерфейс: перейдите в frontend, выполните npm ci и npm run build.'
}
if ($Port -ne 8000) {
    Write-Host "Для этого порта задайте APP_ORIGIN=http://127.0.0.1:$Port в .env."
}

$workerProcess = $null
$botProcess = $null
$logDirectory = [System.IO.Path]::GetFullPath((Join-Path $projectRoot 'data'))
$runnerId = '{0}-{1}' -f $PID, [System.Guid]::NewGuid().ToString('N')
$stopFilePath = [System.IO.Path]::GetFullPath((Join-Path $logDirectory ('runner-{0}.stop' -f $runnerId)))
$expectedStopFileName = [System.IO.Path]::GetFileName($stopFilePath)
Push-Location -LiteralPath $projectRoot
try {
    if (-not $NoWorker -or -not $NoBot) {
        New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
    }
    if (-not $NoWorker) {
        $workerProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-u', '-m', 'backend.worker', '--stop-file', ('"{0}"' -f $stopFilePath)) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDirectory 'worker.stdout.log') -RedirectStandardError (Join-Path $logDirectory 'worker.stderr.log')
    }
    if (-not $NoBot) {
        $botProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-u', '-m', 'backend.telegram_bot', '--stop-file', ('"{0}"' -f $stopFilePath)) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDirectory 'bot.stdout.log') -RedirectStandardError (Join-Path $logDirectory 'bot.stderr.log')
    }
    Write-Host "Invoice Keeper: http://127.0.0.1:$Port"
    Write-Host 'Нажмите Ctrl+C для остановки приложения и запущенных им фоновых процессов.'
    & $pythonPath -m uvicorn backend.app:app --host 127.0.0.1 --port $Port --no-access-log --no-proxy-headers
}
finally {
    $children = @($workerProcess, $botProcess) | Where-Object { $null -ne $_ }
    try {
        if ($children.Count -gt 0) {
            Write-Host 'Останавливаем фоновые процессы после завершения текущих запросов…'
            try {
                Set-Content -LiteralPath $stopFilePath -Value 'stop' -Encoding UTF8
            }
            catch {
                Write-Warning 'Не удалось создать файл остановки. Фоновые процессы будут остановлены после ожидания.'
            }
            $shutdownTimer = [System.Diagnostics.Stopwatch]::StartNew()
            foreach ($child in $children) {
                if (-not $child.HasExited) {
                    $remainingMilliseconds = [int][System.Math]::Max(0, 45000 - $shutdownTimer.ElapsedMilliseconds)
                    if (-not $child.WaitForExit($remainingMilliseconds)) {
                        Write-Warning ('Фоновый процесс {0} не завершился за 45 секунд. Выполняется принудительная остановка.' -f $child.Id)
                        Stop-Process -Id $child.Id -ErrorAction SilentlyContinue
                        $null = $child.WaitForExit(5000)
                    }
                }
            }
        }
    }
    finally {
        $stopFileDirectory = [System.IO.Path]::GetDirectoryName($stopFilePath)
        $allChildrenExited = $true
        foreach ($child in $children) {
            if (-not $child.HasExited) {
                $allChildrenExited = $false
            }
        }
        if ($allChildrenExited -and $stopFileDirectory -eq $logDirectory -and [System.IO.Path]::GetFileName($stopFilePath) -eq $expectedStopFileName) {
            Remove-Item -LiteralPath $stopFilePath -Force -ErrorAction SilentlyContinue
        }
        elseif (-not $allChildrenExited) {
            Write-Warning 'Фоновый процесс ещё работает. Его файл остановки сохранён до завершения процесса.'
        }
        foreach ($child in $children) {
            $child.Dispose()
        }
        Pop-Location
    }
}

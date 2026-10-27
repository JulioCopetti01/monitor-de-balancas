<#
    Gerencia o Monitor de Balancas como tarefa agendada do Windows.

    A tarefa roda como SYSTEM ao ligar o servidor, sem precisar de usuario
    logado, e o iniciar_monitor.bat reinicia o app se ele cair.

    Execute no PowerShell como Administrador, dentro da pasta do projeto:
        .\servico_monitor.ps1 instalar   # instala dependencias, cria a tarefa e inicia
        .\servico_monitor.ps1 status     # mostra se esta rodando
        .\servico_monitor.ps1 reiniciar  # use apos alterar app.py ou config.json
        .\servico_monitor.ps1 parar
        .\servico_monitor.ps1 iniciar
        .\servico_monitor.ps1 remover    # para e apaga a tarefa

    Se a execucao de scripts estiver bloqueada, use:
        powershell -ExecutionPolicy Bypass -File .\servico_monitor.ps1 instalar
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("instalar", "iniciar", "parar", "reiniciar", "status", "remover")]
    [string]$Acao
)

$ErrorActionPreference = "Stop"
$NomeTarefa = "MonitorBalancas"
$Pasta = $PSScriptRoot
$Bat = Join-Path $Pasta "iniciar_monitor.bat"
$AppPy = Join-Path $Pasta "app.py"
$Requisitos = Join-Path $Pasta "requirements.txt"

function Ler-Porta {
    # Mesma porta que o app.py usa: config.json, senao o exemplo, senao 8080
    foreach ($nome in @("config.json", "config.example.json")) {
        $arquivo = Join-Path $Pasta $nome
        if (Test-Path $arquivo) {
            try {
                $config = Get-Content -Raw -Encoding UTF8 $arquivo | ConvertFrom-Json
                if ($config.servidor -and $config.servidor.porta) { return [int]$config.servidor.porta }
            } catch {
                Write-Host "Aviso: nao foi possivel ler $nome ($($_.Exception.Message))."
            }
            break
        }
    }
    return 8080
}

function Encontrar-Python {
    # A tarefa roda como SYSTEM, que nao enxerga o "py"/"python" do perfil
    # do usuario. Por isso grava o caminho completo do python.exe.
    $ErrorActionPreference = "Continue"
    foreach ($comando in @("py", "python")) {
        if (-not (Get-Command $comando -ErrorAction SilentlyContinue)) { continue }
        try {
            $exe = & $comando -c "import sys; print(sys.executable)" 2>$null
        } catch {
            continue
        }
        if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Path ([string]$exe).Trim())) {
            return ([string]$exe).Trim()
        }
    }
    throw "Python nao encontrado. Instale o Python 3 (python.org) marcando 'Add python.exe to PATH' e rode de novo."
}

function Parar-Monitor {
    if (Get-ScheduledTask -TaskName $NomeTarefa -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $NomeTarefa
    }
    # Encerrar a tarefa nao fecha o python iniciado pelo .bat, entao
    # encerra os processos que estao rodando este .bat / este app.py
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.CommandLine -and (
                $_.CommandLine -like "*$Bat*" -or $_.CommandLine -like "*$AppPy*"
            )
        } |
        ForEach-Object {
            Write-Host "Encerrando processo $($_.ProcessId) ($($_.Name))"
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
}

function Mostrar-Status {
    $tarefa = Get-ScheduledTask -TaskName $NomeTarefa -ErrorAction SilentlyContinue
    if (-not $tarefa) {
        Write-Host "Tarefa '$NomeTarefa' nao instalada."
        return
    }
    Write-Host "Tarefa '$NomeTarefa': $($tarefa.State)"

    $porta = Ler-Porta
    $conexao = Get-NetTCPConnection -LocalPort $porta -State Listen -ErrorAction SilentlyContinue
    if ($conexao) {
        Write-Host "Monitor respondendo na porta $porta (processo $($conexao[0].OwningProcess))."
        Write-Host "TV:    http://localhost:$porta"
        Write-Host "Painel: http://localhost:$porta/admin"
    } else {
        Write-Host "Nada escutando na porta $porta. Veja o log em $Pasta\logs\monitor.log"
    }
}

switch ($Acao) {
    "instalar" {
        $python = Encontrar-Python
        Write-Host "Python: $python"

        # pip escreve avisos no stderr; nao tratar isso como erro fatal
        $ErrorActionPreference = "Continue"
        Write-Host "Instalando dependencias (requirements.txt)..."
        & $python -m pip install -r $Requisitos
        if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar as dependencias do requirements.txt." }
        $ErrorActionPreference = "Stop"

        Parar-Monitor

        $porta = Ler-Porta
        if (Get-NetTCPConnection -LocalPort $porta -State Listen -ErrorAction SilentlyContinue) {
            throw "A porta $porta ja esta em uso. Feche o app que esta usando a porta (ou um 'python app.py' aberto) ou troque servidor.porta no config.json."
        }

        $acaoTarefa = New-ScheduledTaskAction -Execute "cmd.exe" `
            -Argument "/c `"`"$Bat`" `"$python`"`"" -WorkingDirectory $Pasta
        $gatilho = New-ScheduledTaskTrigger -AtStartup
        $usuario = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
        $config = New-ScheduledTaskSettingsSet `
            -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -StartWhenAvailable -MultipleInstances IgnoreNew

        Register-ScheduledTask -TaskName $NomeTarefa -Action $acaoTarefa -Trigger $gatilho `
            -Principal $usuario -Settings $config -Force `
            -Description "Monitor de Balancas - $Pasta" | Out-Null

        Start-ScheduledTask -TaskName $NomeTarefa
        Start-Sleep -Seconds 5
        Write-Host "Tarefa instalada e iniciada."
        Mostrar-Status
    }
    "iniciar" {
        Start-ScheduledTask -TaskName $NomeTarefa
        Start-Sleep -Seconds 5
        Mostrar-Status
    }
    "parar" {
        Parar-Monitor
        Write-Host "Monitor parado."
    }
    "reiniciar" {
        Parar-Monitor
        Start-ScheduledTask -TaskName $NomeTarefa
        Start-Sleep -Seconds 5
        Mostrar-Status
    }
    "status" {
        Mostrar-Status
    }
    "remover" {
        Parar-Monitor
        Unregister-ScheduledTask -TaskName $NomeTarefa -Confirm:$false
        Write-Host "Tarefa removida."
    }
}

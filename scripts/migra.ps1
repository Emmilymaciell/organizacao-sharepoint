# migra.ps1 - painel de comando da migracao pasta 5 -> PARCEIRO INFRA (independe do Claude).
#
#   .\scripts\migra.ps1 iniciar  <LOTE>   abre o runner (339) num processo proprio, minimizado
#   .\scripts\migra.ps1 status            painel por quantidade e bytes + runner vivo + fim do log
#   .\scripts\migra.ps1 pausar            termina os arquivos em voo e para (arquivo PAUSAR)
#   .\scripts\migra.ps1 retomar  <LOTE>   remove PAUSAR e inicia de novo (continua de onde parou)
#   .\scripts\migra.ps1 parar             pausa e espera o runner encerrar com seguranca
#   .\scripts\migra.ps1 falhas            excecoes que precisam de revisao humana
#   .\scripts\migra.ps1 relatorio         gera status.json, excecoes.csv e validados.csv
#   .\scripts\migra.ps1 agendar  <LOTE>   tarefa no Agendador: retoma o lote ao entrar no Windows
#   .\scripts\migra.ps1 desagendar        remove essa tarefa
#
# Nada aqui autoriza lote: o 336 continua exigindo fase e aprovacao escrita.
$ErrorActionPreference = 'Continue'
$acao = if ($args.Count -gt 0) { [string]$args[0] } else { 'status' }
$lote = if ($args.Count -gt 1) { [string]$args[1] } else { '' }
$raiz = Split-Path -Parent $PSScriptRoot
$tmp = Join-Path $raiz 'tmp\migracao_parceiro'
$pausar = Join-Path $tmp 'PAUSAR'
$arqPid = Join-Path $tmp 'runner.pid'
$runner = Join-Path $raiz 'scripts\339_migra_rodar_lote.ps1'
$tarefa = 'Programa Migracao PARCEIRO'
if (-not (Test-Path $tmp)) { New-Item -ItemType Directory -Path $tmp | Out-Null }

function Runner-Vivo {
    if (-not (Test-Path $arqPid)) { return $null }
    $id = [int](Get-Content $arqPid -Raw)
    $p = Get-Process -Id $id -ErrorAction SilentlyContinue
    if ($p) { return $p } else { return $null }
}

function Exigir-Lote {
    if ([string]::IsNullOrWhiteSpace($lote)) { Write-Host 'Informe o lote. Ex.: .\scripts\migra.ps1 iniciar PILOTO-02'; exit 2 }
}

function Iniciar {
    Exigir-Lote
    $vivo = Runner-Vivo
    if ($vivo) { Write-Host ('Ja existe um runner ativo (PID ' + $vivo.Id + '). Use status, pausar ou parar.'); return }
    $p = Start-Process powershell.exe -WindowStyle Minimized -PassThru -WorkingDirectory $raiz `
        -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $runner + '"'), '-Lotes', ('"' + $lote + '"'))
    Set-Content -Path $arqPid -Value $p.Id -Encoding Ascii
    Write-Host ('Runner iniciado (PID ' + $p.Id + ') para o lote ' + $lote + '. Pode fechar o Claude; acompanhe com: .\scripts\migra.ps1 status')
}

Set-Location $raiz
switch ($acao) {
    'iniciar' { Iniciar }
    'retomar' {
        if (Test-Path $pausar) { Remove-Item $pausar }
        Iniciar
    }
    'pausar' {
        Set-Content -Path $pausar -Value (Get-Date -Format s) -Encoding Ascii
        Write-Host 'PAUSAR criado: os arquivos em voo terminam e nada novo comeca. Retomar: .\scripts\migra.ps1 retomar <LOTE>'
    }
    'parar' {
        Set-Content -Path $pausar -Value (Get-Date -Format s) -Encoding Ascii
        $vivo = Runner-Vivo
        if (-not $vivo) { Write-Host 'Nenhum runner ativo.'; break }
        Write-Host ('Aguardando o runner (PID ' + $vivo.Id + ') terminar os arquivos em voo...')
        $vivo.WaitForExit()
        Write-Host 'Runner encerrado com seguranca. Retomar: .\scripts\migra.ps1 retomar <LOTE>'
    }
    'status' {
        & py -3.12 -X utf8 scripts\338_migra_status.py
        $vivo = Runner-Vivo
        if ($vivo) { Write-Host ('Runner ATIVO: PID ' + $vivo.Id + ' desde ' + $vivo.StartTime) } else { Write-Host 'Runner: nenhum ativo.' }
        if (Test-Path $pausar) { Write-Host 'PAUSAR presente (nada novo comeca).' }
        $ultimo = Get-ChildItem (Join-Path $raiz 'logs') -Filter 'migra_*.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime | Select-Object -Last 1
        if ($ultimo) { Write-Host ('--- fim de ' + $ultimo.Name); Get-Content $ultimo.FullName -Encoding UTF8 -Tail 8 }
    }
    'falhas' {
        & py -3.12 -X utf8 scripts\338_migra_status.py | Out-Null
        $csv = Join-Path $raiz 'output\migracao_parceiro\excecoes.csv'
        $linhas = @(Import-Csv $csv -Delimiter ';' -Encoding UTF8)
        Write-Host ('Excecoes: ' + $linhas.Count + '  (' + $csv + ')')
        $linhas | Group-Object estado | ForEach-Object { Write-Host ('  ' + $_.Name + ': ' + $_.Count) }
        $linhas | Select-Object -First 20 estado, origem, ultimo_motivo | Format-Table -AutoSize -Wrap
    }
    'relatorio' {
        & py -3.12 -X utf8 scripts\338_migra_status.py
        Write-Host 'Arquivos em output\migracao_parceiro: status.json, excecoes.csv, validados.csv, progresso.json'
    }
    'agendar' {
        Exigir-Lote
        # Roda como a PROPRIA Emmily e so com ela logada: e o unico contexto em que o DPAPI decifra a senha.
        $usuario = $env:USERDOMAIN + '\' + $env:USERNAME
        $acaoT = New-ScheduledTaskAction -Execute 'powershell.exe' -WorkingDirectory $raiz `
            -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File "' + $runner + '" -Lotes "' + $lote + '"')
        $gatilho = New-ScheduledTaskTrigger -AtLogOn -User $usuario
        $princ = New-ScheduledTaskPrincipal -UserId $usuario -LogonType Interactive -RunLevel Limited
        $conf = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
        Register-ScheduledTask -TaskName $tarefa -Action $acaoT -Trigger $gatilho -Principal $princ -Settings $conf -Force | Out-Null
        Write-Host ('Tarefa "' + $tarefa + '" criada: ao entrar no Windows, retoma o lote ' + $lote + ' (usuario ' + $usuario + ', sem SYSTEM).')
    }
    'desagendar' {
        Unregister-ScheduledTask -TaskName $tarefa -Confirm:$false -ErrorAction SilentlyContinue
        Write-Host ('Tarefa "' + $tarefa + '" removida (se existia).')
    }
    default { Write-Host 'Acoes: iniciar <LOTE> | status | pausar | retomar <LOTE> | parar | falhas | relatorio | agendar <LOTE> | desagendar' }
}

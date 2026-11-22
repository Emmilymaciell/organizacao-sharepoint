# ============================================================
# 369_g2_rodar.ps1 - roda o executor G2 (367) fora do terminal, pelo Agendador de Tarefas.
#
#   -Instalar  registra a tarefa (usuario atual, so com sessao aberta): dispara agora e a cada 15 min
#              (watchdog). Instancia nova enquanto outra roda: ignorada pelo Agendador (IgnoreNew) e, se passar,
#              recusada pela trava de arquivo do 367 (saida 6). Prioridade da tarefa: 7 (abaixo do normal).
#   -Remover   desativa a tarefa (nao apaga): use ao concluir ou para pausa longa.
#   (sem args) execucao do executor: e o que a tarefa chama.
#
# Duas formas (29/10/2026):
#   -Rodovia RD-103                     tarefa "Programa Migracao PARCEIRO G2 - RD-103" (uma rodovia)
#   -Rodovias "RD-102A,RD-104,RD-101"   tarefa "Programa Migracao PARCEIRO G2 - FILA": as rodovias em sequencia, uma de
#                                       cada vez (um so escritor). Rodovia concluida (saida 0): passa a seguinte.
#                                       Pausa/recursos (3) ou executor ja ativo (6): para e a tarefa tenta em 15 min.
#                                       Bloqueio (1): registra e passa a seguinte.
#
# O G2 e SECUNDARIO: o 367 so envia com capacidade ociosa (governador de recursos) e sai sozinho, preservando
# o estado, se ficar pausado por falta de recursos mais de 15 min.
# Nao toca nas tarefas "Programa Migracao PARCEIRO*" da migracao anterior (nomes diferentes; nada e reativado).
# Pausa: criar tmp\migracao_parceiro_g2\PAUSAR  ·  Retomar: apagar o arquivo (a tarefa retoma em ate 15 min).
# ============================================================
param(
    [string]$Rodovia = "",
    [string]$Rodovias = "",
    [string]$Sufixo = "",          # opcional: nome proprio da tarefa (ex.: "FILA 2"); padrao "FILA"
    [switch]$Instalar,
    [switch]$Remover
)
$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
if ($Rodovias) {
    $nome = "Programa Migracao PARCEIRO G2 - $(if ($Sufixo) { $Sufixo } else { 'FILA' })"
    $lista = $Rodovias.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    $argTarefa = "-Rodovias `"$Rodovias`"" + $(if ($Sufixo) { " -Sufixo `"$Sufixo`"" } else { "" })
} elseif ($Rodovia) {
    $nome = "Programa Migracao PARCEIRO G2 - $Rodovia"
    $lista = @($Rodovia)
    $argTarefa = "-Rodovia $Rodovia"
} else {
    throw "informe -Rodovia RD-xxx ou -Rodovias 'RD-a,RD-b'"
}

if ($Instalar) {
    $acao = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSCommandPath`" $argTarefa" `
        -WorkingDirectory $raiz
    $gatilho = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(20) `
        -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 30)
    $conf = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -Priority 7 `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 72)
    Register-ScheduledTask -TaskName $nome -Action $acao -Trigger $gatilho -Settings $conf `
        -Description "Operacao migracao_parceiro_g2 ($($lista -join ', ')): executor 367, um escritor, retomavel, secundario (governador de recursos)." -Force | Out-Null
    Get-ScheduledTask -TaskName $nome | Select-Object TaskName, State
    return
}
if ($Remover) {
    Disable-ScheduledTask -TaskName $nome | Select-Object TaskName, State
    return
}

$log = Join-Path $raiz "logs\migracao_parceiro_g2\369_rodar.log"
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
$env:PYTHONIOENCODING = "utf-8"
if (Test-Path (Join-Path $raiz "tmp\migracao_parceiro_g2\PAUSAR")) {
    Add-Content $log "$(Get-Date -Format s) pausado (PAUSAR existe); nada a fazer"
    return
}
$todasConcluidas = $true
foreach ($go in $lista) {
    Add-Content $log "$(Get-Date -Format s) inicio executor $go (pid $PID)"
    # a saida do Python ja vai para logs\migracao_parceiro_g2\LOG_OPERACIONAL_G2.log (UTF-8); aqui so o codigo
    # stderr em arquivo proprio por instancia: o da instancia ativa fica aberto e travaria a seguinte (29/10/2026)
    $err = Join-Path $raiz "logs\migracao_parceiro_g2\367_stderr_$($go)_$PID.log"
    & python -W ignore (Join-Path $raiz "scripts\367_g2_executar.py") --rodovia $go > $null 2> $err
    $codigo = $LASTEXITCODE
    Add-Content $log "$(Get-Date -Format s) fim executor $go`: saida $codigo"
    if ($codigo -eq 0) { continue }
    $todasConcluidas = $false
    if ($codigo -eq 1) { Add-Content $log "$(Get-Date -Format s) $go BLOQUEADO (ver LOG_OPERACIONAL_G2.log); seguindo"; continue }
    break                                   # 3 (pausa/recursos/retomavel) ou 6 (outro executor): tenta em 15 min
}
if ($todasConcluidas) {
    Add-Content $log "$(Get-Date -Format s) fila concluida ($($lista -join ', ')): desativando a tarefa $nome"
    Disable-ScheduledTask -TaskName $nome | Out-Null
}

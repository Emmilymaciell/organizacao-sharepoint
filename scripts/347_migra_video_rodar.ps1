# 347_migra_video_rodar.ps1 - executor autonomo do envio retomavel do video (346), independe do Claude.
#
# Rodado pela tarefa "Programa Migracao PARCEIRO - Video" a cada 15 min (e ao entrar no Windows). A propria
# repeticao da tarefa e o watchdog: se o processo morrer, a proxima rodada retoma pelos blocos que o
# servidor ja tem. Duplicata e impossivel: a tarefa usa IgnoreNew e o 346 tem trava propria.
#
#   powershell -File scripts\347_migra_video_rodar.ps1            executa (ou retoma) o VIDEO-87G
#   powershell -File scripts\347_migra_video_rodar.ps1 -Status    painel: blocos, GiB, velocidade, previsao
#
# Sinais em tmp\migracao_parceiro:
#   PAUSAR_VIDEO      (voce cria) termina os blocos em voo e para; apague para retomar
#   VIDEO_PARADO      (346 saiu 1/4/5) erro estrutural ou divergencia: nao reinicia ate revisao humana
#   VIDEO_CONCLUIDO   arquivo VALIDADO: as rodadas seguintes nao fazem nada
# Blocos de 16 MiB: com 64 MiB o proxy do PARCEIRO derrubava a conexao no meio de blocos lentos (~1 MiB/s).
param([string]$Lote = 'VIDEO-87G', [int]$BlocoMib = 16, [int]$Paralelo = 3, [switch]$Status)
$ErrorActionPreference = 'Continue'
$raiz = Split-Path -Parent $PSScriptRoot
$pastaTmp = Join-Path $raiz 'tmp\migracao_parceiro'
$sinalOk = Join-Path $pastaTmp 'VIDEO_CONCLUIDO'
$sinalParado = Join-Path $pastaTmp 'VIDEO_PARADO'
$sinalPausa = Join-Path $pastaTmp 'PAUSAR_VIDEO'
$arqLog = Join-Path $raiz 'logs\video_rodar.log'
$script346 = Join-Path $raiz 'scripts\346_migra_video_retomavel.py'

function Registrar([string]$texto) {
    $linha = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '  ' + $texto
    [System.IO.File]::AppendAllText($arqLog, $linha + "`r`n", (New-Object System.Text.UTF8Encoding($false)))
}

if ($Status) {
    $ckp = Get-ChildItem -Path $pastaTmp -Filter 'prog-migracao-*.json' -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -notlike '*-teste.json' } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($ckp) {
        $j = [System.IO.File]::ReadAllText($ckp.FullName, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
        $pct = [math]::Round(100 * $j.blocos_no_servidor / $j.blocos, 1)
        Write-Host ('Blocos no servidor : ' + $j.blocos_no_servidor + ' / ' + $j.blocos + ' (' + $pct + '%)')
        Write-Host ('GiB enviados       : ' + $j.GiB_enviados + ' de ' + [math]::Round($j.total / 1GB, 2))
        Write-Host ('Velocidade         : ' + $j.MiB_s + ' MiB/s')
        Write-Host ('Fim do envio prev. : ' + $j.fim_envio_estimado + ' (depois: montagem + conferencia integral)')
        Write-Host ('Checkpoint         : ' + $ckp.FullName + ' (' + $j.atualizado + ')')
    } else { Write-Host 'Sem checkpoint ainda.' }
    foreach ($s in @($sinalOk, $sinalParado, $sinalPausa)) { if (Test-Path $s) { Write-Host ('SINAL: ' + (Split-Path -Leaf $s)) } }
    $vivos = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*346_migra_video_retomavel*--lote*' })
    if ($vivos.Count -gt 0) { Write-Host ('Processo ativo     : PID ' + (($vivos | ForEach-Object { $_.ProcessId }) -join ', ')) }
    else { Write-Host 'Processo ativo     : nenhum (a tarefa retoma em ate 15 min, salvo sinal acima)' }
    $logDia = Get-ChildItem -Path (Join-Path $raiz 'logs') -Filter 'video_retomavel_*.log' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($logDia) { Write-Host ''; Write-Host ('--- ' + $logDia.FullName); Get-Content -Path $logDia.FullName -Encoding UTF8 -Tail 8 }
    exit 0
}

foreach ($s in @($sinalOk, $sinalParado, $sinalPausa)) {
    if (Test-Path $s) { Registrar ('nada a fazer: sinal ' + (Split-Path -Leaf $s)); exit 0 }
}
$python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $python) { Registrar 'python.exe nao encontrado no PATH'; exit 1 }

for ($rodada = 0; $rodada -lt 40; $rodada++) {
    if (Test-Path $sinalPausa) { Registrar 'PAUSAR_VIDEO presente: parando'; exit 0 }
    Registrar ('rodada ' + $rodada + ': 346 --lote ' + $Lote + ' --bloco-mib ' + $BlocoMib + ' --paralelo ' + $Paralelo)
    $p = Start-Process -FilePath $python -WorkingDirectory $raiz -WindowStyle Hidden -PassThru -Wait `
        -ArgumentList @('-X', 'utf8', ('"' + $script346 + '"'), '--lote', $Lote, '--bloco-mib', [string]$BlocoMib, '--paralelo', [string]$Paralelo) `
        -RedirectStandardOutput (Join-Path $raiz 'logs\video_346_saida.log') -RedirectStandardError (Join-Path $raiz 'logs\video_346_erro.log')
    $codigo = $p.ExitCode
    Registrar ('346 saiu com ' + $codigo)
    if ($codigo -eq 0) { Set-Content -Path $sinalOk -Value (Get-Date -Format s) -Encoding Ascii; Registrar 'VIDEO VALIDADO'; exit 0 }
    if ($codigo -eq 6) { Registrar 'outra execucao ativa: esta rodada sai'; exit 0 }
    if ($codigo -ne 3) {
        Set-Content -Path $sinalParado -Value ('346 saiu ' + $codigo + ' em ' + (Get-Date -Format s)) -Encoding Ascii
        Registrar 'PARADO: revisar o log antes de apagar VIDEO_PARADO'
        exit 1
    }
    $espera = [math]::Min(60 * [math]::Pow(2, [math]::Min($rodada, 4)), 900)
    Registrar ('falha transitoria: retoma em ' + $espera + ' s')
    Start-Sleep -Seconds $espera
}
Registrar 'limite de 40 rodadas nesta execucao; a tarefa agendada tenta de novo'
exit 3

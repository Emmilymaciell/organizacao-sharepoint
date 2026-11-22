# 339_migra_rodar_lote.ps1 - executa uma FILA de lotes APROVADOS sem depender da sessao do Claude.
#
# Para cada lote, em ordem:
#   340 (pre-requisitos) -> se falhar, o lote NAO roda: pendencia registrada e segue para o proximo
#   336 (transferir, com conferencia em linha) -> falha recuperavel: espera e nova rodada (ate N)
#   337 (conferencia dos que sobraram) -> 338 --lote (resultado consolidado do lote)
# Parada global (credencial/cota, codigo 3) ou PAUSAR interrompem a fila inteira.
# Retomavel: rodar de novo continua de onde parou (a fila esta no banco).
# Log: logs\migra_<lotes>_<data>.log
#
# Uso:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\339_migra_rodar_lote.ps1 -Lotes "PILOTO-02,PROD-01-PRODUTOS"
param(
    [string]$Lotes = '',
    [string]$Lote = '',
    [int]$Rodadas = 3,
    [int]$EsperaSegundos = 120,
    [int]$Concorrencia = 0
)
$ErrorActionPreference = 'Continue'
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz
if ([string]::IsNullOrWhiteSpace($Lotes)) { $Lotes = $Lote }
$lista = @($Lotes.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if ($lista.Count -eq 0) { Write-Host 'Informe -Lotes "A,B,C"'; exit 2 }
$carimbo = Get-Date -Format 'yyyyMMdd_HHmmss'
$nomeLog = if ($lista.Count -eq 1) { $lista[0] } else { 'fila' }
$log = Join-Path $raiz ('logs\migra_' + $nomeLog + '_' + $carimbo + '.log')
$pausar = Join-Path $raiz 'tmp\migracao_parceiro\PAUSAR'

function Registrar([string]$texto) {
    $linha = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '  ' + $texto
    Write-Host $linha
    Add-Content -Path $log -Value $linha -Encoding UTF8
}

function Rodar([string[]]$argumentos) {
    $saida = & py -3.12 -X utf8 @argumentos 2>&1
    foreach ($l in $saida) { Registrar ('    ' + [string]$l) }
    return $LASTEXITCODE
}

Registrar ('Fila: ' + ($lista -join ', ') + ' (PID ' + $PID + ', log: ' + $log + ')')
$extra = @()
if ($Concorrencia -gt 0) { $extra = @('--concorrencia', [string]$Concorrencia) }
$resumo = @()
$codigoFinal = 0

foreach ($l in $lista) {
    if (Test-Path $pausar) { Registrar 'PAUSAR presente: a fila para aqui.'; $resumo += ($l + ': nao iniciado (pausa)'); $codigoFinal = 2; break }
    Registrar ('===== Lote ' + $l + ': pre-requisitos')
    $pre = Rodar @('scripts\340_migra_prerequisitos.py', $l)
    if ($pre -ne 0) {
        Registrar ('PENDENCIA: pre-requisitos do lote ' + $l + ' nao atendidos. Lote NAO executado; seguindo para o proximo.')
        $resumo += ($l + ': NAO executado (pre-requisitos)'); $codigoFinal = 2
        continue
    }
    $codigo = 1
    for ($i = 1; $i -le $Rodadas; $i++) {
        if (Test-Path $pausar) { Registrar 'PAUSAR presente: nada novo e iniciado.'; break }
        Registrar ('Lote ' + $l + ' - rodada ' + $i + '/' + $Rodadas + ': transferir (com conferencia em linha)')
        $codigo = Rodar (@('scripts\336_migra_transferir.py', $l) + $extra)
        if ($codigo -eq 0) { break }
        if ($codigo -eq 1) { Registrar 'BLOQUEADO (fase, aprovacao ou outra execucao ativa).'; break }
        if ($codigo -eq 3) { break }
        if ($codigo -eq 5) { Registrar 'Erro inesperado no 336 (detalhe acima). Nova rodada retoma.' }
        if ($i -lt $Rodadas) { Registrar ('Falhas recuperaveis; nova rodada em ' + $EsperaSegundos + ' s'); Start-Sleep -Seconds $EsperaSegundos }
    }
    Registrar ('Lote ' + $l + ': conferencia dos que ainda nao foram conferidos')
    $conf = Rodar @('scripts\337_migra_conferir.py', $l)
    [void](Rodar @('scripts\338_migra_status.py', '--lote', $l))
    $resumo += ($l + ': transferencia=' + $codigo + ' conferencia=' + $conf)
    if ($codigo -ne 0 -or $conf -ne 0) { $codigoFinal = 2 }
    if ($codigo -eq 3) {
        Registrar 'PARADA GLOBAL (credencial recusada ou cota cheia): a fila inteira para. Ver o log.'
        $codigoFinal = 3; break
    }
}
Registrar '===== Fim da fila'
foreach ($r in $resumo) { Registrar ('  ' + $r) }
[void](Rodar @('scripts\338_migra_status.py'))
exit $codigoFinal

# 342_migra_encadear.ps1 - inicia a PROXIMA fila de lotes quando a fila ativa terminar.
#
# Roda pelo Agendador de Tarefas (fora do job do Claude). Espera o runner ativo (tmp\migracao_parceiro\runner.pid)
# encerrar; SO ENTAO aplica os ajustes de config pedidos (ex.: blocos maiores para arquivo grande), para nao
# mexer na configuracao de lotes em andamento; e inicia o 339 com a nova fila. Nao inicia nada se:
#   - o arquivo PAUSAR existir;
#   - a fila anterior terminar com parada global (credencial/cota): ver o log dela.
#
# Uso (via Agendador, criado pelo migra.ps1 encadear):
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\342_migra_encadear.ps1 -Lotes "A,B" `
#       -ChunkMiB 64 -TimeoutMin 120 -TamanhoMaxGiB 100
param(
    [Parameter(Mandatory = $true)][string]$Lotes,
    [int]$ChunkMiB = 0,
    [int]$TimeoutMin = 0,
    [int]$TamanhoMaxGiB = 0
)
$ErrorActionPreference = 'Continue'
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz
$tmp = Join-Path $raiz 'tmp\migracao_parceiro'
$log = Join-Path $raiz ('logs\migra_encadear_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.log')
function Registrar([string]$t) { $l = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '  ' + $t; Write-Host $l; Add-Content -Path $log -Value $l -Encoding UTF8 }

Registrar ('Encadeador: proxima fila = ' + $Lotes + ' (PID ' + $PID + ')')
$arqPid = Join-Path $tmp 'runner.pid'
if (Test-Path $arqPid) {
    $id = [int](Get-Content $arqPid -Raw)
    $p = Get-Process -Id $id -ErrorAction SilentlyContinue
    if ($p) { Registrar ('Aguardando a fila ativa (runner PID ' + $id + ') terminar...'); $p.WaitForExit() }
}
# a fila anterior pode ter sido iniciada fora do runner.pid: confere tambem pela linha de comando
do {
    $vivos = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'powershell.exe' -and $_.CommandLine -match '339_migra_rodar_lote' })
    if ($vivos.Count -gt 0) { Start-Sleep -Seconds 60 }
} while ($vivos.Count -gt 0)
Registrar 'Fila anterior encerrada.'

if (Test-Path (Join-Path $tmp 'PAUSAR')) { Registrar 'PAUSAR presente: a nova fila NAO foi iniciada.'; exit 2 }
$ultimo = Get-ChildItem (Join-Path $raiz 'logs') -Filter 'migra_fila_*.log' | Sort-Object LastWriteTime | Select-Object -Last 1
if ($ultimo -and (Select-String -Path $ultimo.FullName -Pattern 'PARADA GLOBAL' -Quiet)) {
    Registrar ('A fila anterior parou por condicao global (ver ' + $ultimo.Name + '). A nova fila NAO foi iniciada.'); exit 3
}

# ajustes de config so agora, com nada em andamento
$yaml = Join-Path $raiz 'config\migracao_parceiro.yaml'
$texto = [IO.File]::ReadAllText($yaml, [Text.Encoding]::UTF8)
$original = $texto
if ($ChunkMiB -gt 0) { $texto = [regex]::Replace($texto, '--webdav-nextcloud-chunk-size, \d+M', ('--webdav-nextcloud-chunk-size, ' + $ChunkMiB + 'M')) }
if ($TimeoutMin -gt 0) { $texto = [regex]::Replace($texto, '--timeout, \d+m', ('--timeout, ' + $TimeoutMin + 'm')) }
if ($TamanhoMaxGiB -gt 0) { $texto = [regex]::Replace($texto, '(?m)^(\s+tamanho_max_gib_por_arquivo:\s*)\d+', ('${1}' + $TamanhoMaxGiB)) }
if ($texto -cne $original) {
    Copy-Item $yaml ($yaml + '.antes_encadear_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
    [IO.File]::WriteAllText($yaml, $texto, (New-Object Text.UTF8Encoding $false))
    Registrar ('Config ajustada: chunk=' + $ChunkMiB + 'M timeout=' + $TimeoutMin + 'm tamanho_max=' + $TamanhoMaxGiB + 'GiB (copia de seguranca ao lado)')
}

$runner = Join-Path $raiz 'scripts\339_migra_rodar_lote.ps1'
$p = Start-Process powershell.exe -WindowStyle Minimized -PassThru -WorkingDirectory $raiz `
    -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $runner + '"'), '-Lotes', ('"' + $Lotes + '"'))
Set-Content -Path $arqPid -Value $p.Id -Encoding Ascii
Registrar ('Nova fila iniciada: runner PID ' + $p.Id)
exit 0

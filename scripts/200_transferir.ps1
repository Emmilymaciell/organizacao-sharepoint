<#
    200_transferir.ps1 - copia pastas do SharePoint Programa para o OneDrive.

    COPIA. NAO MOVE, NAO APAGA, NAO RENOMEIA.
    Este script nao contem nenhum comando de exclusao. A origem permanece
    intacta do inicio ao fim.

    NAO OCUPA DISCO: usa o endpoint /copy do Microsoft Graph, que manda a
    Microsoft copiar de um drive para outro dentro da nuvem dela. Nenhum
    arquivo passa por esta maquina.

    Login: janela oficial da Microsoft. A senha nao passa por este script.

    USO
        # so conferir o acesso, sem copiar nada:
        .\200_transferir.ps1 -SomenteVerificar

        # simular: mostra o que faria, sem copiar:
        .\200_transferir.ps1 -Lote 1 -Simular

        # copiar o lote 1:
        .\200_transferir.ps1 -Lote 1

        # copiar todos, na ordem segura (o maior por ultimo):
        .\200_transferir.ps1 -Todos

    RETOMADA
        O log em logs\transferencia.csv registra cada pasta. Rodar de novo
        pula o que ja esta como OK. Queda nao recomeca do zero.
#>

[CmdletBinding()]
param(
    [int]    $Lote,
    [switch] $Todos,
    [switch] $Simular,
    [switch] $SomenteVerificar,
    [string] $ContaDestino,          # fora do Git: ver Resolver-ContaDestino
    [int]    $PausaMs = 700
)

$ErrorActionPreference = "Stop"
$raiz     = Split-Path -Parent $PSScriptRoot
$pastaPa  = Join-Path $raiz "output\pa"
$pastaLog = Join-Path $raiz "logs"
$log      = Join-Path $pastaLog "transferencia.csv"
# Conta de destino FORA do Git (ADR-0015 do Emmily AI Project OS): o e-mail nao fica no script.
# Ordem: -ContaDestino  >  $env:PROG_CONTA_DESTINO  >  linha PROG_CONTA_DESTINO= do .env
# (o .env ja e ignorado pelo .gitignore e ja guarda as credenciais do Graph).
function Resolver-ContaDestino {
    param([string]$Valor, [string]$Raiz)
    if ($Valor) { return $Valor }
    if ($env:PROG_CONTA_DESTINO) { return $env:PROG_CONTA_DESTINO }
    $arqEnv = Join-Path $Raiz ".env"
    if (Test-Path -LiteralPath $arqEnv) {
        foreach ($l in (Get-Content -LiteralPath $arqEnv -Encoding UTF8)) {
            if ($l -match '^\s*PROG_CONTA_DESTINO\s*=\s*"?([^"#\s]+)"?') { return $Matches[1] }
        }
    }
    return $null
}
$ContaDestino = Resolver-ContaDestino -Valor $ContaDestino -Raiz $raiz
if (-not $ContaDestino) {
    Write-Host " Conta de destino nao definida. Use -ContaDestino <conta>, ou grave" -ForegroundColor Red
    Write-Host " PROG_CONTA_DESTINO=<conta> no .env do projeto (fora do Git)." -ForegroundColor Red
    exit 1
}

$SITE_HOST = "contoso.sharepoint.com"
$SITE_PATH = "/sites/exemplo"
$BIBLIOTECA = "Programa"

# Ordem deliberada: o lote 5 (RD102A, 263 GiB) fica por ultimo.
$ORDEM = @(
    @{ n = 1; arq = "1-interno";  rotulo = "Compartilhado interno" },
    @{ n = 2; arq = "2-produtos"; rotulo = "Produtos"              },
    @{ n = 3; arq = "3-rd101";    rotulo = "RD101"                 },
    @{ n = 6; arq = "6-rd104";    rotulo = "RD104"                 },
    @{ n = 4; arq = "4-rd103";    rotulo = "RD103"                 },
    @{ n = 5; arq = "5-rd102a";   rotulo = "RD102A (o maior)"      }
)

# ----------------------------------------------------------------------
function Escrever { param([string]$t, [string]$cor = "Gray")
    Write-Host $t -ForegroundColor $cor
}

function Registrar {
    param($lote, $acao, $origem, $destino, $status, $detalhe)
    if (-not (Test-Path $pastaLog)) { New-Item -ItemType Directory $pastaLog | Out-Null }
    if (-not (Test-Path $log)) {
        "quando;lote;acao;origem;destino;status;detalhe" |
            Out-File $log -Encoding utf8
    }
    $q = [char]34
    $d = ($detalhe -replace [char]34, [char]39)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $linha = "$ts;$lote;$acao;$q$origem$q;$q$destino$q;$status;$q$d$q"
    Add-Content -Path $log -Value $linha -Encoding utf8
}

function JaFeito {
    param($origem)
    if (-not (Test-Path $log)) { return $false }
    $script:feitos | Where-Object { $_ -eq $origem } | Select-Object -First 1
}

function Graph {
    <# chamada ao Graph com retry em 429/503 (limite de requisicoes) #>
    param($Metodo, $Uri, $Corpo, [int]$Tentativas = 5)
    for ($i = 1; $i -le $Tentativas; $i++) {
        try {
            if ($Corpo) {
                return Invoke-MgGraphRequest -Method $Metodo -Uri $Uri `
                    -Body ($Corpo | ConvertTo-Json -Depth 8) `
                    -ContentType "application/json" -OutputType PSObject
            }
            return Invoke-MgGraphRequest -Method $Metodo -Uri $Uri -OutputType PSObject
        } catch {
            $cod = $_.Exception.Response.StatusCode.value__
            if (($cod -eq 429 -or $cod -eq 503 -or $cod -eq 504) -and $i -lt $Tentativas) {
                $espera = [Math]::Min(60, 5 * $i)
                Escrever "      limite de requisicoes; aguardando $espera s..." "DarkYellow"
                Start-Sleep -Seconds $espera
                continue
            }
            throw
        }
    }
}

function ItemPorCaminho {
    <# devolve o item (pasta) a partir do caminho dentro do drive #>
    param($DriveId, $Caminho)
    $c = ($Caminho -replace '^/', '')
    if ([string]::IsNullOrWhiteSpace($c)) {
        return Graph GET "/v1.0/drives/$DriveId/root"
    }
    $enc = ($c -split '/' | ForEach-Object { [uri]::EscapeDataString($_) }) -join '/'
    return Graph GET "/v1.0/drives/$DriveId/root:/$enc"
}

# ----------------------------------------------------------------------
Escrever ""
Escrever "==================================================================" "Cyan"
Escrever " TRANSFERENCIA Programa -> OneDrive   (COPIA: nada e apagado ou movido)" "Cyan"
Escrever "==================================================================" "Cyan"
Escrever ""

# --- modulo -----------------------------------------------------------
if (-not (Get-Module -ListAvailable Microsoft.Graph.Authentication)) {
    Escrever " Instalando o modulo oficial da Microsoft (so na primeira vez)..." "Yellow"
    Install-Module Microsoft.Graph.Authentication -Scope CurrentUser -Force -AllowClobber
}
Import-Module Microsoft.Graph.Authentication -ErrorAction Stop

# --- login ------------------------------------------------------------
$ctx = Get-MgContext
if (-not $ctx -or $ctx.Account -ne $ContaDestino) {
    Escrever " LOGIN POR CODIGO" "Yellow"
    Escrever " Vai aparecer um codigo abaixo. Abra https://microsoft.com/devicelogin"
    Escrever " no navegador, cole o codigo e entre com:"
    Escrever "     $ContaDestino" "Yellow"
    Escrever " (a senha e digitada no site da Microsoft; este script nao a ve)"
    Escrever ""
    # -UseDeviceAuthentication: o login por codigo funciona em qualquer
    # terminal. O login por janela (WAM) falha com "window handle must be
    # configured" quando o PowerShell roda dentro de outro programa.
    Connect-MgGraph -Scopes "Files.ReadWrite.All","Sites.ReadWrite.All" `
        -UseDeviceAuthentication -NoWelcome
    $ctx = Get-MgContext
}
Escrever " Conectada como: $($ctx.Account)" "Green"
if ($ctx.Account -ne $ContaDestino) {
    Escrever ""
    Escrever " ATENCAO: a conta conectada NAO e $ContaDestino." "Red"
    Escrever " As pastas iriam parar no OneDrive errado. Abortando." "Red"
    Escrever " Rode  Disconnect-MgGraph  e tente de novo." "Red"
    exit 1
}

# --- origem e destino -------------------------------------------------
Escrever ""
Escrever " Localizando origem e destino..."
$site = Graph GET "/v1.0/sites/${SITE_HOST}:${SITE_PATH}"
$drives = Graph GET "/v1.0/sites/$($site.id)/drives"
$dOrigem = $drives.value | Where-Object { $_.name -eq $BIBLIOTECA } | Select-Object -First 1
if (-not $dOrigem) { Escrever " Biblioteca '$BIBLIOTECA' nao encontrada." "Red"; exit 1 }

$dDestino = Graph GET "/v1.0/me/drive"

Escrever "   origem : $($site.displayName) / $($dOrigem.name)" "Green"
Escrever "   destino: $($dDestino.name)  ($($dDestino.owner.user.displayName))" "Green"

$raizDestino = Graph GET "/v1.0/drives/$($dDestino.id)/root"

if ($SomenteVerificar) {
    Escrever ""
    Escrever " Acesso conferido. Nenhuma copia foi feita." "Green"
    Escrever " Rode com  -Lote 1 -Simular  para ver o que seria copiado."
    exit 0
}

# --- o que foi feito antes (retomada) ---------------------------------
$script:feitos = @()
if (Test-Path $log) {
    $script:feitos = Import-Csv $log -Delimiter ';' |
        Where-Object { $_.status -eq "OK" -and $_.acao -eq "COPIAR" } |
        ForEach-Object { $_.origem }
    if ($script:feitos.Count) {
        Escrever ""
        Escrever " $($script:feitos.Count) pasta(s) ja copiadas antes - serao puladas." "DarkCyan"
    }
}

# --- quais lotes ------------------------------------------------------
$lotes = if ($Todos) { $ORDEM } elseif ($Lote) { $ORDEM | Where-Object { $_.n -eq $Lote } }
         else { Escrever " Informe -Lote N ou -Todos." "Red"; exit 1 }
if (-not $lotes) { Escrever " Lote $Lote nao existe." "Red"; exit 1 }

$totOk = 0; $totErro = 0; $totPulado = 0

foreach ($l in $lotes) {
    $fCriar  = Join-Path $pastaPa "$($l.arq)_criar_pastas.json"
    $fCopiar = Join-Path $pastaPa "$($l.arq)_copiar.json"
    if (-not (Test-Path $fCopiar)) { Escrever " falta $fCopiar" "Red"; continue }

    $criar  = Get-Content $fCriar  -Raw -Encoding utf8 | ConvertFrom-Json
    $copiar = Get-Content $fCopiar -Raw -Encoding utf8 | ConvertFrom-Json

    Escrever ""
    Escrever "------------------------------------------------------------------" "Cyan"
    Escrever " LOTE $($l.n) - $($l.rotulo)" "Cyan"
    Escrever "   $($criar.Count) pasta(s) a criar - $($copiar.Count) pasta(s) a copiar" "Cyan"
    Escrever "------------------------------------------------------------------" "Cyan"

    # ---- 1) criar a estrutura (pai antes do filho) -------------------
    Escrever ""
    Escrever " [1/2] estrutura de pastas no destino"
    foreach ($p in $criar) {
        if ($Simular) { Escrever "   [simular] criaria  $p" "DarkGray"; continue }
        try {
            $partes = $p -split '/'
            $nome   = $partes[-1]
            $paiCam = if ($partes.Count -gt 1) { ($partes[0..($partes.Count-2)] -join '/') } else { "" }
            $pai    = ItemPorCaminho $dDestino.id $paiCam
            $corpo  = @{ name = $nome; folder = @{}; "@microsoft.graph.conflictBehavior" = "fail" }
            Graph POST "/v1.0/drives/$($dDestino.id)/items/$($pai.id)/children" $corpo | Out-Null
            Escrever "   criada   $p" "Green"
            Registrar $l.n "CRIAR" $p "" "OK" ""
        } catch {
            $m = $_.Exception.Message
            if ($m -match "nameAlreadyExists|already exists|accessDenied.*exists") {
                Escrever "   ja existe $p" "DarkGray"
                Registrar $l.n "CRIAR" $p "" "JA_EXISTE" ""
            } else {
                Escrever "   ERRO ao criar $p  -> $m" "Red"
                Registrar $l.n "CRIAR" $p "" "ERRO" $m
            }
        }
        Start-Sleep -Milliseconds $PausaMs
    }

    # ---- 2) copiar -----------------------------------------------------
    Escrever ""
    Escrever " [2/2] copiando pastas (server-side; nada passa por este PC)"
    $i = 0
    foreach ($item in $copiar) {
        $i++
        $org = $item.origem
        $dst = $item.destino_pai
        $rotulo = if ($org.Length -gt 62) { "..." + $org.Substring($org.Length - 59) } else { $org }

        if ($script:feitos -contains $org) {
            Escrever "   [$i/$($copiar.Count)] pulada (ja copiada)  $rotulo" "DarkGray"
            $totPulado++; continue
        }
        if ($Simular) {
            Escrever "   [$i/$($copiar.Count)] [simular] $rotulo" "DarkGray"
            Escrever "                  -> $dst" "DarkGray"
            continue
        }

        try {
            $itOrigem = ItemPorCaminho $dOrigem.id $org
            $itPai    = ItemPorCaminho $dDestino.id $dst
            $corpo = @{
                parentReference = @{ driveId = $dDestino.id; id = $itPai.id }
                name            = ($org -split '/')[-1]
            }
            $enc = "/v1.0/drives/$($dOrigem.id)/items/$($itOrigem.id)/copy"
            Graph POST $enc $corpo | Out-Null
            Escrever "   [$i/$($copiar.Count)] iniciada  $rotulo" "Green"
            Registrar $l.n "COPIAR" $org $dst "OK" "copia iniciada no servidor"
            $totOk++
        } catch {
            $m = $_.Exception.Message
            Escrever "   [$i/$($copiar.Count)] ERRO  $rotulo" "Red"
            Escrever "        $m" "Red"
            Registrar $l.n "COPIAR" $org $dst "ERRO" $m
            $totErro++
        }
        Start-Sleep -Milliseconds $PausaMs
    }
}

# ----------------------------------------------------------------------
Escrever ""
Escrever "==================================================================" "Cyan"
if ($Simular) {
    Escrever " SIMULACAO - nada foi copiado." "Yellow"
} else {
    $cor = if ($totErro -gt 0) { "Yellow" } else { "Green" }
    Escrever " copias iniciadas: $totOk    erros: $totErro    puladas: $totPulado" $cor
    Escrever ""
    Escrever " O Graph copia em segundo plano. Pastas grandes levam mais tempo"
    Escrever " para aparecer completas no OneDrive."
    Escrever ""
    Escrever " log: $log"
    Escrever ""
    Escrever " A ORIGEM NAO FOI ALTERADA. Nada foi apagado, movido ou renomeado." "Green"
    Escrever " Nao remova nada da origem antes da conferencia por hash." "Yellow"
}
Escrever "==================================================================" "Cyan"
Escrever ""

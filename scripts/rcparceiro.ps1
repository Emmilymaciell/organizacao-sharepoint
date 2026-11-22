# rcparceiro.ps1 - rclone da migracao pasta 5 -> PARCEIRO INFRA (docs/migracao_parceiro/ARQUITETURA.md).
#
# Remotes definidos SO no ambiente deste processo (nenhum rclone.conf e lido ou gravado,
# os remotes de outros projetos ficam intocados):
#   prog:                 SharePoint, biblioteca Programa (credenciais do .env, igual a rc.sh)
#   parceiro_infra_migracao:  Nextcloud PARCEIRO por WebDAV, usuario usuario, SENHA DE APLICATIVO
#                         (a grafia parceiro-infra-migracao: tambem e aceita e traduzida)
#
# A senha de aplicativo fica cifrada com DPAPI (conta Windows da Emmily) em
#   %APPDATA%\prog-migracao-parceiro\parceiro-app.senha.dpapi
# Nunca aparece em argumento, log, banco, Git ou historico do terminal.
#
# Uso:
#   .\scripts\rcparceiro.ps1 -LoginNavegador          # recomendado: aprova no navegador, servidor entrega login+senha
#   .\scripts\rcparceiro.ps1 -DefinirSenha            # alternativa: digitar a senha de app (oculta) e cifrar
#   .\scripts\rcparceiro.ps1 lsd parceiro-infra-migracao:  # qualquer comando rclone permitido na fase
#
# Travas (sempre): move, moveto, sync, bisync, delete, deletefile, purge, rmdir, rmdirs,
# cleanup, dedupe, config show/dump, flags --delete-*.
# Fase 'diagnostico' (config/migracao_parceiro.yaml): so comandos de leitura.

# Sem bloco param de proposito: assim flags do rclone (-v, --max-depth...) chegam intactas em $args
# e nao sao confundidas com parametros comuns do PowerShell (-Verbose etc.).
$ArgsRclone = @($args | ForEach-Object { [string]$_ })
$DefinirSenha = ($ArgsRclone.Count -eq 1 -and $ArgsRclone[0] -eq '-DefinirSenha')
$LoginNavegador = ($ArgsRclone.Count -eq 1 -and $ArgsRclone[0] -eq '-LoginNavegador')
$ErrorActionPreference = 'Stop'

$raizRepo = Split-Path -Parent $PSScriptRoot
$pastaSegredo = Join-Path $env:APPDATA 'prog-migracao-parceiro'
$arqSenha = Join-Path $pastaSegredo 'parceiro-app.senha.dpapi'
$usuarioParceiro = 'usuario'
$urlParceiro = 'https://nextcloud.example.com/remote.php/dav/files/' + $usuarioParceiro + '/'
# Gancho de diagnostico: so aceita servidor LOCAL (host exatamente 127.0.0.1, sem usuario na URL),
# para inspecionar o que o rclone envia. Qualquer outro valor e RECUSADO (nao cai no PARCEIRO em silencio).
if ($env:RCPARCEIRO_URL_TESTE) {
    $u = $null
    $okTeste = [Uri]::TryCreate($env:RCPARCEIRO_URL_TESTE, [UriKind]::Absolute, [ref]$u) -and $u.Scheme -ceq 'http' -and
               $u.Host -ceq '127.0.0.1' -and [string]::IsNullOrEmpty($u.UserInfo)
    if (-not $okTeste) { throw 'BLOQUEADO: RCPARCEIRO_URL_TESTE so aceita http://127.0.0.1:<porta>/... sem usuario na URL.' }
    $urlParceiro = $u.AbsoluteUri
}
$exeRclone = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages\Rclone.Rclone_Microsoft.Winget.Source_8wekyb3d8bbwe\rclone-v1.75.1-windows-amd64\rclone.exe'
if (-not (Test-Path $exeRclone -PathType Leaf)) { $exeRclone = (Get-Command rclone).Source }

function Proteger-Pasta([string]$pasta) {
    if (-not (Test-Path $pasta -PathType Container)) { New-Item -ItemType Directory -Path $pasta | Out-Null }
    $conta = $env:USERDOMAIN + '\' + $env:USERNAME
    $null = & icacls $pasta /inheritance:r /grant:r ($conta + ':(OI)(CI)F') /grant:r 'SYSTEM:(OI)(CI)F'
    if ($LASTEXITCODE -ne 0) { throw 'icacls falhou ao restringir a pasta do segredo.' }
}

function Texto-DeSegura([securestring]$seg) {
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($seg)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

if ($DefinirSenha) {
    Proteger-Pasta $pastaSegredo
    Write-Host 'Cole ou digite a SENHA DE APLICATIVO do Nextcloud PARCEIRO (nao aparece na tela).'
    $s1 = Read-Host -AsSecureString 'Senha de aplicativo'
    $s2 = Read-Host -AsSecureString 'Repita'
    $t1 = Texto-DeSegura $s1
    $t2 = Texto-DeSegura $s2
    $iguais = [string]::Equals($t1, $t2, [StringComparison]::Ordinal)
    $vazia = [string]::IsNullOrWhiteSpace($t1)
    $t1 = $null; $t2 = $null
    if (-not $iguais) { throw 'As duas digitacoes nao conferem. Nada foi gravado.' }
    if ($vazia) { throw 'Senha vazia. Nada foi gravado.' }
    # ConvertFrom-SecureString sem -Key usa DPAPI do usuario atual
    $s1 | ConvertFrom-SecureString | Set-Content -Path $arqSenha -Encoding Ascii
    Write-Host ('Senha cifrada (DPAPI) em ' + $arqSenha)
    Write-Host 'Teste somente leitura: .\scripts\rcparceiro.ps1 lsd parceiro-infra-migracao:'
    exit 0
}

if ($LoginNavegador) {
    # Login Flow v2 do Nextcloud (o mesmo dos clientes oficiais): a Emmily aprova no navegador,
    # ja logada; o servidor devolve o NOME DE LOGIN exato e uma senha de aplicativo nova direto
    # para este processo. Sem copiar/colar e sem adivinhar o login. A senha vai so para o DPAPI.
    Proteger-Pasta $pastaSegredo
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $site = 'https://nextcloud.example.com'
    $agente = 'Rclone - Migracao Programa PARCEIRO (login pelo navegador)'
    $inicio = Invoke-RestMethod -Method Post -Uri ($site + '/index.php/login/v2') -UserAgent $agente
    Write-Host 'Abrindo o navegador. Confira o endereco nextcloud.example.com, clique em Entrar/Login'
    Write-Host 'e depois em "Conceder acesso". Esta janela espera ate 10 minutos.'
    $uLogin = $null
    if (-not ([Uri]::TryCreate([string]$inicio.login, [UriKind]::Absolute, [ref]$uLogin) -and $uLogin.Scheme -ceq 'https' -and
              $uLogin.Host -ceq 'nextcloud.example.com')) { throw 'Endereco de login inesperado devolvido pelo servidor. Nada foi feito.' }
    Start-Process $uLogin.AbsoluteUri
    $resp = $null
    $limite = (Get-Date).AddMinutes(10)
    while (-not $resp -and (Get-Date) -lt $limite) {
        Start-Sleep -Seconds 3
        try {
            $resp = Invoke-RestMethod -Method Post -Uri $inicio.poll.endpoint -Body @{ token = $inicio.poll.token } -UserAgent $agente
        } catch {
            $codigo = 0
            if ($_.Exception.Response) { $codigo = [int]$_.Exception.Response.StatusCode }
            if ($codigo -ne 404) { throw ('Falha ao consultar a aprovacao (HTTP ' + $codigo + ').') }
        }
    }
    $inicio = $null
    if (-not $resp) { throw 'Tempo esgotado: o acesso nao foi concedido no navegador. Nada foi gravado.' }
    $uSrv = $null
    if (-not ([Uri]::TryCreate([string]$resp.server, [UriKind]::Absolute, [ref]$uSrv) -and $uSrv.Host -ceq 'nextcloud.example.com')) {
        throw 'Servidor inesperado na resposta do login. Nada foi gravado.'
    }
    ConvertTo-SecureString ([string]$resp.appPassword) -AsPlainText -Force | ConvertFrom-SecureString |
        Set-Content -Path $arqSenha -Encoding Ascii
    $loginServidor = [string]$resp.loginName
    $resp = $null
    $arqYaml = Join-Path $raizRepo 'config\migracao_parceiro.yaml'
    $texto = [IO.File]::ReadAllText($arqYaml, [Text.Encoding]::UTF8)
    $texto = [regex]::Replace($texto, '(?m)^(\s+login:\s*)"[^"\r\n]*"', ('$1"' + $loginServidor.Replace('$', '$$') + '"'))
    [IO.File]::WriteAllText($arqYaml, $texto, (New-Object Text.UTF8Encoding $false))
    Write-Host ('Senha de aplicativo nova cifrada (DPAPI) em ' + $arqSenha)
    Write-Host ('Nome de login informado pelo servidor: ' + $loginServidor + '  (gravado em config\migracao_parceiro.yaml)')
    Write-Host 'Teste somente leitura: .\scripts\rcparceiro.ps1 lsd parceiro-infra-migracao:'
    exit 0
}

if ($ArgsRclone.Count -eq 0) { Write-Host 'Uso: .\scripts\rcparceiro.ps1 -DefinirSenha | -LoginNavegador | -Permissoes <pasta>... | <comando rclone> ...'; exit 2 }

if ($ArgsRclone[0] -eq '-Permissoes') {
    # SOMENTE LEITURA: PROPFIND Depth 0 pedindo as permissoes do Nextcloud de cada pasta.
    # Letras (oc:permissions): G ler, C criar arquivo, K criar pasta, W escrever, D apagar,
    # N renomear, V mover, R recompartilhar, S compartilhado, M montado.
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $seg = (Get-Content -Path $arqSenha -Encoding Ascii -Raw).Trim() | ConvertTo-SecureString
    $yamlP = [IO.File]::ReadAllText((Join-Path $raizRepo 'config\migracao_parceiro.yaml'), [Text.Encoding]::UTF8)
    $loginP = ([regex]::Match($yamlP, '(?m)^\s+login:\s*"?([^"\r\n#]+?)"?\s*(#.*)?$')).Groups[1].Value.Trim()
    $plano = Texto-DeSegura $seg
    $cab = 'Basic ' + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($loginP + ':' + $plano))
    $plano = $null
    $corpo = '<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns"><d:prop><oc:permissions/><nc:share-permissions/><oc:owner-display-name/><nc:mount-type/><oc:size/></d:prop></d:propfind>'
    foreach ($pasta in ($ArgsRclone | Select-Object -Skip 1)) {
        $partes = $pasta.Split('/') | ForEach-Object { [Uri]::EscapeDataString($_) }
        $req = [Net.HttpWebRequest]::Create($urlParceiro + ($partes -join '/'))
        $req.Method = 'PROPFIND'; $req.Headers['Authorization'] = $cab; $req.Headers['Depth'] = '0'
        $req.ContentType = 'application/xml'
        $bytes = [Text.Encoding]::UTF8.GetBytes($corpo)
        $st = $req.GetRequestStream(); $st.Write($bytes, 0, $bytes.Length); $st.Close()
        try {
            $resp = $req.GetResponse()
            $xml = [xml](New-Object IO.StreamReader($resp.GetResponseStream(), [Text.Encoding]::UTF8)).ReadToEnd()
            $resp.Close()
            $ns = New-Object Xml.XmlNamespaceManager($xml.NameTable)
            $ns.AddNamespace('d', 'DAV:'); $ns.AddNamespace('oc', 'http://owncloud.org/ns'); $ns.AddNamespace('nc', 'http://nextcloud.org/ns')
            $perm = $xml.SelectSingleNode('//oc:permissions', $ns).InnerText
            $sp = $xml.SelectSingleNode('//nc:share-permissions', $ns).InnerText
            $dono = $xml.SelectSingleNode('//oc:owner-display-name', $ns).InnerText
            $mt = $xml.SelectSingleNode('//nc:mount-type', $ns).InnerText
            $pode = @(); if ($perm -cmatch 'K') { $pode += 'cria pasta' }; if ($perm -cmatch 'C') { $pode += 'cria arquivo' }
            if ($perm -cmatch 'W') { $pode += 'altera' }; if ($perm -cmatch 'N') { $pode += 'renomeia' }; if ($perm -cmatch 'D') { $pode += 'apaga' }
            Write-Output ($pasta + ' | permissions=' + $perm + ' | share-permissions=' + $sp + ' | dono=' + $dono + ' | montagem=' + $mt + ' | ' + ($pode -join ', '))
        } catch [Net.WebException] {
            $codigo = 0; if ($_.Exception.Response) { $codigo = [int]$_.Exception.Response.StatusCode }
            Write-Output ($pasta + ' | ERRO HTTP ' + $codigo)
        }
    }
    $cab = $null
    exit 0
}

# ---- travas: LISTA DO QUE E PERMITIDO, por fase ---------------------------
# O subcomando tem de ser o PRIMEIRO argumento (flag antes dele e recusada), e so passa o que
# esta na lista. Escrita no PARCEIRO so existe pelo 'rcd', usado pelo motor com a API restrita
# (rc.py: so stat/copyfile, com Immutable). copy, copyto, move, sync, delete, purge, mount,
# serve, touch, rcat, config etc. nunca passam.
$sub = $ArgsRclone[0].ToLowerInvariant()
if ($sub.StartsWith('-')) { throw ('BLOQUEADO: o subcomando do rclone deve vir primeiro (recebido: ' + $ArgsRclone[0] + ').') }
$yaml = [IO.File]::ReadAllText((Join-Path $raizRepo 'config\migracao_parceiro.yaml'), [Text.Encoding]::UTF8)
$fase = ([regex]::Match($yaml, '(?m)^fase:\s*(\w+)')).Groups[1].Value
$leitura = @('lsd', 'ls', 'lsf', 'lsl', 'lsjson', 'size', 'about', 'hashsum', 'md5sum', 'sha1sum', 'check', 'cat', 'version', 'listremotes', 'backend', 'tree', 'help')
$permitidos = $leitura
if ($fase -ceq 'piloto' -or $fase -ceq 'execucao') { $permitidos = $leitura + @('rcd') }
if (-not ($permitidos -contains $sub)) {
    throw ('BLOQUEADO: "' + $sub + '" nao e permitido na fase ' + $fase + '. Permitidos: ' + ($permitidos -join ', ') + '.')
}
if ($sub -eq 'backend' -and ($ArgsRclone.Count -lt 2 -or $ArgsRclone[1] -ne 'features')) {
    throw 'BLOQUEADO: so "backend features" e permitido.'
}
for ($i = 1; $i -lt $ArgsRclone.Count; $i++) {
    $a = $ArgsRclone[$i].ToLowerInvariant()
    foreach ($f in @('--delete', '--config', '--dump', '--rc-no-auth', '--rc-user', '--rc-pass', '--rc-web', '--rc-files',
                     '--password-command', '--ask-password', '--inplace', '--ignore-times', '--no-check-dest')) {
        if ($a.StartsWith($f)) { throw ('BLOQUEADO: flag ' + $ArgsRclone[$i]) }
    }
    if ($a.StartsWith('--rc-addr')) {
        $valor = if ($a.Contains('=')) { $a.Split('=', 2)[1] } elseif ($i + 1 -lt $ArgsRclone.Count) { $ArgsRclone[$i + 1] } else { '' }
        if (-not $valor.StartsWith('127.0.0.1:')) { throw 'BLOQUEADO: a API do rcd so pode escutar em 127.0.0.1' }
    }
}

# ---- remote do SharePoint (mesma origem de rc.sh) ----------------------
$envArq = Join-Path $raizRepo '.env'
$valores = @{}
foreach ($linha in [IO.File]::ReadAllLines($envArq, [Text.Encoding]::UTF8)) {
    $m = [regex]::Match($linha, '^\s*([A-Z_]+)\s*=\s*(.*?)\s*$')
    if ($m.Success) { $valores[$m.Groups[1].Value] = $m.Groups[2].Value }
}
$env:RCLONE_CONFIG_PROG_TYPE = 'onedrive'
$env:RCLONE_CONFIG_PROG_CLIENT_ID = $valores['GRAPH_CLIENT_ID']
$env:RCLONE_CONFIG_PROG_CLIENT_SECRET = $valores['GRAPH_CLIENT_SECRET']
$env:RCLONE_CONFIG_PROG_CLIENT_CREDENTIALS = 'true'
$env:RCLONE_CONFIG_PROG_TENANT = $valores['GRAPH_TENANT_ID']
$env:RCLONE_CONFIG_PROG_DRIVE_ID = '00000000-0000-0000-0000-000000000000'
$env:RCLONE_CONFIG_PROG_DRIVE_TYPE = 'documentLibrary'

# ---- remote do PARCEIRO ---------------------------------------------------
if (Test-Path $arqSenha -PathType Leaf) {
    $seg = (Get-Content -Path $arqSenha -Encoding Ascii -Raw).Trim() | ConvertTo-SecureString
    $plano = Texto-DeSegura $seg
    # SEM ISTO A AUTENTICACAO FALHA: no powershell -NoProfile o pipe para programa nativo
    # prefixava um BOM (U+FEFF) e o rclone enviava 73 caracteres para uma senha de 72 -> 401.
    # Diagnosticado em 19/10/2026 com um servidor WebDAV falso em 127.0.0.1 (sem tentar no PARCEIRO).
    # O BOM vinha da codificacao de ENTRADA do console; as duas sao fixadas sem preambulo.
    $semBom = New-Object System.Text.UTF8Encoding $false
    $OutputEncoding = $semBom
    try { [Console]::InputEncoding = $semBom } catch { }
    $oculta = $plano | & $exeRclone obscure -
    $plano = $null
    if ($LASTEXITCODE -ne 0) { throw 'rclone obscure falhou.' }
    $env:RCLONE_CONFIG_PARCEIRO_INFRA_MIGRACAO_TYPE = 'webdav'
    $env:RCLONE_CONFIG_PARCEIRO_INFRA_MIGRACAO_VENDOR = 'nextcloud'
    $env:RCLONE_CONFIG_PARCEIRO_INFRA_MIGRACAO_URL = $urlParceiro
    # A senha de aplicativo do Nextcloud fica presa ao NOME DE LOGIN usado ao cria-la, que pode
    # diferir do identificador da URL (ex.: e-mail). Vem de destino.login no config (nao e segredo).
    $login = ([regex]::Match($yaml, '(?m)^\s+login:\s*"?([^"\r\n#]+?)"?\s*(#.*)?$')).Groups[1].Value.Trim()
    if ([string]::IsNullOrWhiteSpace($login)) { $login = $usuarioParceiro }
    $env:RCLONE_CONFIG_PARCEIRO_INFRA_MIGRACAO_USER = $login
    $env:RCLONE_CONFIG_PARCEIRO_INFRA_MIGRACAO_PASS = ($oculta | Out-String).Trim()
    $oculta = $null
} elseif (($ArgsRclone -join ' ') -match 'parceiro[-_]infra[-_]migracao:') {
    throw 'Senha de aplicativo ainda nao cadastrada. Rode: .\scripts\rcparceiro.ps1 -DefinirSenha'
}

# O rclone 1.75 NAO acha remote definido por variavel de ambiente quando o nome tem hifen
# (RCLONE_CONFIG_PARCEIRO_INFRA_MIGRACAO_* so casa com "parceiro_infra_migracao:"). O nome interno e
# com sublinhado; a grafia com hifen continua aceita aqui e e traduzida.
$ArgsRclone = @($ArgsRclone | ForEach-Object { $_ -replace '^parceiro-infra-migracao:', 'parceiro_infra_migracao:' })

try {
    & $exeRclone --config NUL @ArgsRclone
    $codigo = $LASTEXITCODE
} finally {
    foreach ($k in @('PASS', 'USER', 'URL', 'VENDOR', 'TYPE')) { Remove-Item ('Env:\RCLONE_CONFIG_PARCEIRO_INFRA_MIGRACAO_' + $k) -ErrorAction SilentlyContinue }
    Remove-Item Env:\RCLONE_CONFIG_PROG_CLIENT_SECRET -ErrorAction SilentlyContinue
}
exit $codigo

-- ============================================================
-- Migracao G2 (Engenharia) -> PARCEIRO INFRA  ·  data/migracao_parceiro_g2.db (fora do Git)
-- Independente de data/migracao_parceiro.db e de data/organizacao.db.
-- ============================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS execucao (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    script      TEXT NOT NULL,
    inicio      TEXT NOT NULL,
    fim         TEXT,
    estado      TEXT NOT NULL DEFAULT 'andamento',   -- andamento | concluida | erro | interrompida
    parametros  TEXT,
    resumo      TEXT
);

-- EAP (156 nos do recorte G2), carregada do manifesto conferido contra o Excel
CREATE TABLE IF NOT EXISTS eap_no (
    caminho     TEXT PRIMARY KEY,        -- relativo a PARCEIRO-INFRA, ex.: "03. RD-101/04. Engenharia/01. Projetos"
    rodovia     TEXT NOT NULL,
    nivel       INTEGER NOT NULL,
    rotulo      TEXT NOT NULL,
    descricao   TEXT,
    linha_eap   INTEGER,
    celula_flux TEXT,
    tem_nota    INTEGER NOT NULL DEFAULT 0
);

-- ------------------------------------------------------------
-- PARCEIRO: fotografias das quatro arvores Engenharia (PROPFIND)
-- tipo: baseline (imutavel, antes de qualquer escrita) | monitor | final
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS parceiro_foto (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo        TEXT NOT NULL,
    inicio      TEXT NOT NULL,
    fim         TEXT,
    arquivos    INTEGER,
    pastas      INTEGER,
    bytes       INTEGER,
    erros       INTEGER,
    sha256_csv  TEXT,                    -- hash do CSV exportado desta foto
    execucao_id INTEGER REFERENCES execucao(id)
);

CREATE TABLE IF NOT EXISTS parceiro_item (
    foto_id     INTEGER NOT NULL REFERENCES parceiro_foto(id),
    caminho     TEXT NOT NULL,           -- relativo a raiz WebDAV do usuario, ex.: "PARCEIRO-INFRA/03. RD-101/..."
    rodovia     TEXT,
    eh_pasta    INTEGER NOT NULL,
    tamanho     INTEGER,
    modificado  TEXT,                    -- getlastmodified (RFC 1123)
    etag        TEXT,
    fileid      TEXT,                    -- oc:fileid: identificador estavel no Nextcloud
    checksums   TEXT,                    -- oc:checksums, quando o servidor tiver
    permissoes  TEXT,                    -- oc:permissions
    dono        TEXT,                    -- oc:owner-display-name (dono do armazenamento, NAO autoria)
    PRIMARY KEY (foto_id, caminho)
);
CREATE INDEX IF NOT EXISTS ix_parceiro_fileid ON parceiro_item(foto_id, fileid);

-- erros de listagem nunca somem (regra 5)
CREATE TABLE IF NOT EXISTS parceiro_erro (
    foto_id INTEGER NOT NULL REFERENCES parceiro_foto(id),
    caminho TEXT NOT NULL,
    erro    TEXT NOT NULL,
    em      TEXT NOT NULL
);

-- hash de conteudo de preexistentes (lido do PARCEIRO por streaming, so quando necessario)
CREATE TABLE IF NOT EXISTS parceiro_hash (
    fileid      TEXT NOT NULL,
    etag        TEXT NOT NULL,           -- o hash vale para este etag
    caminho     TEXT NOT NULL,
    tamanho     INTEGER,
    quick_xor   TEXT,
    sha256      TEXT,
    lido_em     TEXT NOT NULL,
    PRIMARY KEY (fileid, etag)
);

-- ------------------------------------------------------------
-- ORIGEM (SharePoint Programa e as 4 pastas de BM), pelo Graph, so GET
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS origem_item (
    drive_id        TEXT NOT NULL,
    item_id         TEXT NOT NULL,
    fonte           TEXT NOT NULL,       -- ex.: Programa, BM_RD101
    pai_id          TEXT,
    caminho         TEXT NOT NULL,       -- relativo a raiz da fonte
    nome            TEXT NOT NULL,
    eh_pasta        INTEGER NOT NULL,
    tamanho         INTEGER,
    criado_em       TEXT,
    modificado_em   TEXT,
    modificado_por  TEXT,
    etag            TEXT,
    ctag            TEXT,
    quick_xor_hash  TEXT,
    web_url         TEXT,
    inventariado_em TEXT NOT NULL,
    execucao_id     INTEGER REFERENCES execucao(id),
    PRIMARY KEY (drive_id, item_id)
);
CREATE INDEX IF NOT EXISTS ix_g2_origem_caminho ON origem_item(fonte, caminho);
CREATE INDEX IF NOT EXISTS ix_g2_origem_hash    ON origem_item(quick_xor_hash);

CREATE TABLE IF NOT EXISTS origem_fila (
    execucao_id INTEGER NOT NULL REFERENCES execucao(id),
    drive_id    TEXT NOT NULL,
    item_id     TEXT NOT NULL,
    fonte       TEXT NOT NULL,
    caminho     TEXT NOT NULL,
    listada_em  TEXT,
    erro        TEXT,
    PRIMARY KEY (execucao_id, drive_id, item_id)
);

-- ------------------------------------------------------------
-- MATRIZ origem -> destino (uma linha por arquivo de origem considerado)
-- classificacao: JA_EXISTIA_CORRETO | JA_EXISTIA_NOME_INCORRETO | JA_EXISTIA_PASTA_INCORRETA |
--   JA_EXISTIA_VERSAO_DIFERENTE | JA_EXISTIA_EM_OUTRO_DESTINO | DUPLICIDADE_CONFIRMADA |
--   EQUIVALENCIA_INCERTA | AUSENTE_E_ELEGIVEL | NAO_APROVADO | SEM_EVIDENCIA | FORA_DO_ESCOPO |
--   ALTERADO_DURANTE_EXECUCAO | CONFLITO_CONCORRENTE
-- estado (execucao): PLANEJADO | NA_FILA | COPIANDO | COPIADO | VALIDADO | PULADO | ERRO | DIVERGENTE
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plano (
    drive_id        TEXT NOT NULL,
    item_id         TEXT NOT NULL,
    rodovia         TEXT,
    categoria       TEXT,                -- ANTEPROJETO | EXECUTIVO | BM | LICENCIAMENTO | DESAPROPRIACAO | DIARIO | REL_SEMANAL | REL_MENSAL | NC | SST
    regra           TEXT,                -- nota da EAP / regra aplicada
    evidencia       TEXT,                -- linha de controle, nota de aprovacao, etc.
    revisao         TEXT,
    aprovacao       TEXT,
    classificacao   TEXT NOT NULL,
    motivo          TEXT,
    destino_rel     TEXT,                -- caminho final completo, relativo a raiz WebDAV
    correspondente  TEXT,                -- caminho PARCEIRO equivalente, quando houver
    lote            TEXT,
    estado          TEXT NOT NULL DEFAULT 'PLANEJADO',
    tentativas      INTEGER NOT NULL DEFAULT 0,
    erro            TEXT,
    etag_origem     TEXT,                -- etag da origem no momento do planejamento
    fileid_destino  TEXT,
    sha256          TEXT,
    quick_xor_dest  TEXT,
    copiado_em      TEXT,
    validado_em     TEXT,
    atualizado_em   TEXT NOT NULL,
    PRIMARY KEY (drive_id, item_id, destino_rel)
);
CREATE INDEX IF NOT EXISTS ix_plano_estado ON plano(estado, lote);

-- pastas da EAP a criar (MKCOL), cada uma registrada
CREATE TABLE IF NOT EXISTS pasta_criada (
    caminho     TEXT PRIMARY KEY,
    motivo      TEXT NOT NULL,
    criada_em   TEXT,
    fileid      TEXT,
    resultado   TEXT                     -- CRIADA | JA_EXISTIA | RECUSADA
);

-- cada tentativa de escrita (auditoria do motor)
CREATE TABLE IF NOT EXISTS envio (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    drive_id    TEXT NOT NULL,
    item_id     TEXT NOT NULL,
    destino_rel TEXT NOT NULL,
    lote        TEXT,
    inicio      TEXT NOT NULL,
    fim         TEXT,
    metodo      TEXT,                    -- PUT_INM | CHUNK_V2
    http        INTEGER,
    resultado   TEXT,                    -- CRIADO | JA_OCUPADO | ORIGEM_ALTERADA | PASTA_INVALIDA | ERRO
    bytes       INTEGER,
    segundos    REAL,
    detalhe     TEXT
);

-- alteracoes externas observadas no PARCEIRO (comparacao entre fotos)
CREATE TABLE IF NOT EXISTS alteracao_externa (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fileid          TEXT,
    caminho_inicial TEXT,
    caminho_atual   TEXT,
    tamanho_inicial INTEGER,
    tamanho_atual   INTEGER,
    etag_inicial    TEXT,
    etag_atual      TEXT,
    tipo            TEXT NOT NULL,       -- NOVO | MODIFICADO | RENOMEADO_OU_MOVIDO | REMOVIDO | INDETERMINADO
    primeira_vez    TEXT NOT NULL,
    ultima_vez      TEXT NOT NULL,
    foto_inicial    INTEGER,
    foto_atual      INTEGER,
    nosso           INTEGER NOT NULL DEFAULT 0,  -- 1 = criado pelo nosso motor (fileid registrado em plano/envio)
    impacto         TEXT,
    UNIQUE (fileid, tipo, caminho_atual)
);

-- ------------------------------------------------------------
-- Governador de recursos (29/10/2026): uma linha por mudanca de estado e a cada ~60 s.
-- O G2 e secundario: so trabalha com capacidade ociosa e cede ao projeto prioritario.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS recurso (
    em                 TEXT NOT NULL,
    rodovia            TEXT,
    estado             TEXT NOT NULL,     -- EXECUTANDO | REDUZINDO_CARGA | PAUSA_POR_RECURSOS | ...
    workers            INTEGER,
    ativos             INTEGER,
    motivo             TEXT,
    ram_livre_mb       INTEGER,
    commit_pct         REAL,
    cpu_pct            REAL,
    prioritario_proc   INTEGER,
    prioritario_ws_mb  INTEGER,
    disco_livre_gib    REAL,
    validados          INTEGER,
    bytes_validados    INTEGER
);

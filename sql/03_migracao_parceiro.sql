-- ============================================================
-- Migracao "5. Compartilhado PARCEIRO" -> PARCEIRO INFRA (Nextcloud)
-- Banco proprio: data/migracao_parceiro.db (fora do Git).
-- Independente de data/organizacao.db: a migracao inicia, pausa,
-- retoma e e auditada sem tocar no catalogo global.
-- ============================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Cada execucao de script do modulo (auditoria e retomada)
CREATE TABLE IF NOT EXISTS execucao (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    script      TEXT NOT NULL,
    inicio      TEXT NOT NULL,
    fim         TEXT,
    estado      TEXT NOT NULL DEFAULT 'andamento',   -- andamento | concluida | erro | interrompida
    parametros  TEXT,
    resumo      TEXT
);

-- ------------------------------------------------------------
-- ORIGEM: pasta "5. Compartilhado PARCEIRO" da biblioteca Programa, pelo Graph
-- caminho_rel e relativo a raiz da pasta 5 (separador "/")
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS origem_item (
    item_id         TEXT PRIMARY KEY,
    drive_id        TEXT NOT NULL,
    pai_id          TEXT,
    caminho_rel     TEXT NOT NULL,
    nome            TEXT NOT NULL,
    extensao        TEXT,
    eh_pasta        INTEGER NOT NULL,
    tamanho         INTEGER,
    filhos          INTEGER,
    criado_em       TEXT,
    modificado_em   TEXT,
    criado_por      TEXT,
    modificado_por  TEXT,
    etag            TEXT,
    ctag            TEXT,
    quick_xor_hash  TEXT,                -- calculado pelo servidor (algoritmo QuickXorHash)
    mime_type       TEXT,
    web_url         TEXT,
    inventariado_em TEXT NOT NULL,
    execucao_id     INTEGER REFERENCES execucao(id),
    ausente_desde   TEXT                 -- sumiu da origem numa varredura posterior
);
CREATE INDEX IF NOT EXISTS ix_origem_caminho ON origem_item(caminho_rel);
CREATE INDEX IF NOT EXISTS ix_origem_hash    ON origem_item(quick_xor_hash);
CREATE INDEX IF NOT EXISTS ix_origem_pai     ON origem_item(pai_id);

-- Fila da varredura por pastas (retomada apos queda)
CREATE TABLE IF NOT EXISTS origem_fila (
    execucao_id INTEGER NOT NULL REFERENCES execucao(id),
    item_id     TEXT NOT NULL,
    caminho_rel TEXT NOT NULL,
    listada_em  TEXT,
    erro        TEXT,
    PRIMARY KEY (execucao_id, item_id)
);

-- ------------------------------------------------------------
-- DESTINO: arvore observada no PARCEIRO INFRA (somente leitura)
-- caminho relativo a raiz PARCEIRO-INFRA (separador "/")
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS destino_item (
    caminho         TEXT PRIMARY KEY,
    eh_pasta        INTEGER NOT NULL,
    tamanho         INTEGER,
    modificado_em   TEXT,
    inventariado_em TEXT NOT NULL,
    execucao_id     INTEGER REFERENCES execucao(id)
);

-- ------------------------------------------------------------
-- EAP oficial (EAP_Repositorio.xlsx) - arvore expandida
-- modelo = 1 quando o no e um padrao repetivel ([AAAA-MM - Mes] etc.)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS eap_no (
    arvore      TEXT NOT NULL,              -- principal | consorciadas
    caminho     TEXT NOT NULL,              -- "02. Programa/03. Prestacao de Contas/..."
    nome        TEXT NOT NULL,
    nivel       INTEGER NOT NULL,
    pai         TEXT,
    modelo      INTEGER NOT NULL DEFAULT 0,
    codigo      TEXT,
    descricao   TEXT,
    ordem       INTEGER NOT NULL,
    PRIMARY KEY (arvore, caminho)
);

-- Pastas de destino: reusar a existente, criar (com autorizacao) ou equivalencia a decidir
CREATE TABLE IF NOT EXISTS pasta_destino (
    caminho_eap     TEXT PRIMARY KEY,       -- caminho segundo a EAP
    caminho_real    TEXT,                   -- caminho existente no PARCEIRO que atende (pode diferir)
    situacao        TEXT NOT NULL,          -- existe | equivalente | ausente
    acao            TEXT NOT NULL,          -- reusar | criar | decidir
    observacao      TEXT,
    aprovada_em     TEXT
);

-- ------------------------------------------------------------
-- PLANO: uma linha por arquivo da origem. Nenhum arquivo fica sem situacao.
-- estado: INVENTARIADO, CLASSIFICADO, AGUARDANDO_APROVACAO, APROVADO,
--         EM_TRANSFERENCIA, COPIADO, EM_VERIFICACAO, VALIDADO, FALHOU,
--         CONFLITO, FORA_DO_ESCOPO, PENDENTE_DE_DECISAO, HOMOLOGADO
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plano_item (
    item_id         TEXT PRIMARY KEY REFERENCES origem_item(item_id),
    estado          TEXT NOT NULL,
    escopo          TEXT NOT NULL,          -- migrar | fora | pendente
    regra           TEXT,
    categoria       TEXT,
    destino_pasta   TEXT,                   -- caminho final da pasta no PARCEIRO
    nome_final      TEXT,
    prioridade      INTEGER,
    conflito        TEXT,
    lote            TEXT,
    aprovado_em     TEXT,
    atualizado_em   TEXT NOT NULL,
    observacao      TEXT
);
CREATE INDEX IF NOT EXISTS ix_plano_estado ON plano_item(estado);

-- Lotes: proposta -> aprovacao escrita da Emmily -> execucao
CREATE TABLE IF NOT EXISTS lote (
    nome            TEXT PRIMARY KEY,
    criado_em       TEXT NOT NULL,
    criterio        TEXT NOT NULL,
    piloto          INTEGER NOT NULL DEFAULT 0,
    arquivos        INTEGER,
    bytes           INTEGER,
    pastas_a_criar  INTEGER,
    aprovado_em     TEXT,
    aprovacao_texto TEXT                    -- citacao literal da autorizacao
);

-- Cada tentativa de copia (o historico nao e reescrito)
CREATE TABLE IF NOT EXISTS transferencia (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     TEXT NOT NULL,
    lote        TEXT,
    tentativa   INTEGER NOT NULL,
    inicio      TEXT NOT NULL,
    fim         TEXT,
    bytes       INTEGER,
    segundos    REAL,
    resultado   TEXT,                       -- COPIADO | FALHOU | CONFLITO | JA_EXISTIA
    erro        TEXT,
    execucao_id INTEGER REFERENCES execucao(id)
);
CREATE INDEX IF NOT EXISTS ix_transf_item ON transferencia(item_id);

-- Conferencia: bytes lidos do DESTINO (e da origem quando nao ha hash do servidor)
CREATE TABLE IF NOT EXISTS verificacao (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id         TEXT NOT NULL,
    quando          TEXT NOT NULL,
    caminho_destino TEXT NOT NULL,
    bytes_destino   INTEGER,
    qxh_destino     TEXT,                   -- QuickXorHash calculado sobre os bytes do PARCEIRO
    sha256_destino  TEXT,
    sha256_origem   TEXT,                   -- so quando a origem foi lida (sem hash do servidor)
    metodo          TEXT,                   -- qxh_servidor_x_destino | sha256_origem_x_destino
    resultado       TEXT NOT NULL,          -- VALIDADO | DIVERGENTE | AUSENTE | ORIGEM_ALTERADA | ERRO
    detalhe         TEXT,
    execucao_id     INTEGER REFERENCES execucao(id)
);
CREATE INDEX IF NOT EXISTS ix_verif_item ON verificacao(item_id);

-- Uma execucao por vez sobre a fila
CREATE TABLE IF NOT EXISTS trava (
    nome        TEXT PRIMARY KEY,
    pid         INTEGER NOT NULL,
    host        TEXT NOT NULL,
    desde       TEXT NOT NULL,
    batida      REAL NOT NULL               -- time.time() da ultima batida
);

-- Historico de transicoes (so cresce)
CREATE TABLE IF NOT EXISTS evento (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     TEXT,
    quando      TEXT NOT NULL,
    de_estado   TEXT,
    para_estado TEXT,
    detalhe     TEXT,
    execucao_id INTEGER REFERENCES execucao(id)
);
CREATE INDEX IF NOT EXISTS ix_evento_item ON evento(item_id);

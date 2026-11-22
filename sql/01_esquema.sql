-- ============================================================
-- Programa - Organizacao do SharePoint
-- Esquema do catalogo (E1 - inventario pela nuvem)
-- ============================================================

PRAGMA journal_mode = WAL;

-- ------------------------------------------------------------
-- Bibliotecas (drives) do site
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS biblioteca (
    drive_id        TEXT PRIMARY KEY,
    nome            TEXT,
    tipo            TEXT,
    web_url         TEXT,
    quota_usada     INTEGER,   -- cota do SITE (igual em todos os drives)
    quota_total     INTEGER,
    quota_lixeira   INTEGER,
    raiz_tamanho    INTEGER,   -- size do root: soma dos arquivos VIVOS
    raiz_filhos     INTEGER,
    visto_em        TEXT
);

-- ------------------------------------------------------------
-- Catalogo de itens (arquivos e pastas)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS item_nuvem (
    drive_id        TEXT NOT NULL,
    item_id         TEXT NOT NULL,
    biblioteca      TEXT,
    pai_id          TEXT,
    caminho         TEXT,      -- caminho completo dentro da biblioteca
    pasta_pai       TEXT,
    nome            TEXT,
    extensao        TEXT,      -- minuscula, com ponto
    eh_pasta        INTEGER NOT NULL DEFAULT 0,
    eh_raiz         INTEGER NOT NULL DEFAULT 0,
    nivel1          TEXT,      -- 1a pasta do caminho  (para a E3)
    nivel2          TEXT,      -- 2a pasta do caminho
    profundidade    INTEGER,
    tamanho         INTEGER,
    filhos          INTEGER,
    criado_em       TEXT,
    modificado_em   TEXT,
    criado_por      TEXT,
    criado_por_email TEXT,
    modificado_por  TEXT,
    modificado_por_email TEXT,
    quick_xor_hash  TEXT,
    sha1_hash       TEXT,
    sha256_hash     TEXT,
    mime_type       TEXT,
    web_url         TEXT,
    etag            TEXT,
    ctag            TEXT,
    deletado        INTEGER NOT NULL DEFAULT 0,
    visto_em        TEXT,
    PRIMARY KEY (drive_id, item_id)
);

CREATE INDEX IF NOT EXISTS ix_item_hash    ON item_nuvem(quick_xor_hash)
       WHERE quick_xor_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_item_tamanho ON item_nuvem(tamanho);
CREATE INDEX IF NOT EXISTS ix_item_ext     ON item_nuvem(extensao);
CREATE INDEX IF NOT EXISTS ix_item_nivel1  ON item_nuvem(nivel1, nivel2);
CREATE INDEX IF NOT EXISTS ix_item_caminho ON item_nuvem(caminho);
CREATE INDEX IF NOT EXISTS ix_item_pasta   ON item_nuvem(eh_pasta, deletado);

-- ------------------------------------------------------------
-- Controle da varredura: permite retomar de onde parou
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS varredura (
    drive_id        TEXT PRIMARY KEY,
    biblioteca      TEXT,
    estado          TEXT,      -- pendente | andamento | concluida | erro
    link_proximo    TEXT,      -- @odata.nextLink  (retomada no meio)
    delta_link      TEXT,      -- @odata.deltaLink (incremental futuro)
    itens           INTEGER DEFAULT 0,
    paginas         INTEGER DEFAULT 0,
    inicio          TEXT,
    fim             TEXT,
    mensagem        TEXT
);

-- ------------------------------------------------------------
-- REGRA 5: nada some do catalogo. Item com erro e registrado.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS erro (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    quando          TEXT,
    etapa           TEXT,
    drive_id        TEXT,
    item_id         TEXT,
    caminho         TEXT,
    codigo          TEXT,
    mensagem        TEXT
);

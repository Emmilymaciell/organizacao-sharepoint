-- ============================================================
-- Programa - Organizacao do SharePoint
-- Esquema do historico de versoes (E3-b)
--
-- O SharePoint cobra na cota TODAS as versoes de um arquivo, nao so a
-- atual. O `size` do driveItem e so a versao corrente. A diferenca entre
-- a cota do site e a soma dos arquivos vivos mora aqui.
-- ============================================================

-- ------------------------------------------------------------
-- Uma linha por versao de arquivo (GET /items/{id}/versions)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS versao_item (
    drive_id        TEXT NOT NULL,
    item_id         TEXT NOT NULL,
    versao_id       TEXT NOT NULL,
    eh_atual        INTEGER NOT NULL DEFAULT 0,  -- a versao corrente do arquivo
    tamanho         INTEGER,
    modificado_em   TEXT,
    modificado_por  TEXT,
    visto_em        TEXT,
    PRIMARY KEY (drive_id, item_id, versao_id)
);

CREATE INDEX IF NOT EXISTS ix_versao_item ON versao_item(drive_id, item_id);

-- ------------------------------------------------------------
-- Resultado por arquivo consultado: evita reconsultar e permite
-- retomar a amostragem de onde parou (REGRA 7).
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS versao_resumo (
    drive_id        TEXT NOT NULL,
    item_id         TEXT NOT NULL,
    estrato         TEXT,      -- faixa de tamanho amostrada
    n_versoes       INTEGER,   -- total, incluindo a atual
    bytes_total     INTEGER,   -- soma de TODAS as versoes
    bytes_atual     INTEGER,   -- so a versao corrente
    bytes_antigas   INTEGER,   -- bytes_total - bytes_atual  <- o recuperavel
    consultado_em   TEXT,
    erro            TEXT,      -- REGRA 5: falhou, fica registrado com o motivo
    PRIMARY KEY (drive_id, item_id)
);

CREATE INDEX IF NOT EXISTS ix_vresumo_estrato ON versao_resumo(estrato);
CREATE INDEX IF NOT EXISTS ix_vresumo_antigas ON versao_resumo(bytes_antigas);

-- ------------------------------------------------------------
-- Controle da amostragem por estrato: quantos arquivos existem,
-- quantos foram medidos, e o que isso permite extrapolar.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS versao_estrato (
    estrato         TEXT PRIMARY KEY,
    min_bytes       INTEGER,
    max_bytes       INTEGER,
    arquivos_total  INTEGER,   -- quantos arquivos existem na faixa
    bytes_total     INTEGER,   -- soma dos vivos na faixa
    arquivos_medidos INTEGER,  -- quantos tiveram /versions consultado
    bytes_medidos   INTEGER,   -- soma dos vivos entre os medidos
    bytes_antigas_medidos INTEGER,
    atualizado_em   TEXT
);

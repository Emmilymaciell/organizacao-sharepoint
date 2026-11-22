"""
367_g2_executar - executor PERSISTENTE da operacao G2 (um unico escritor). Copia para o PARCEIRO so os itens
AUSENTE_E_ELEGIVEL do plano da rodovia, revalidando tudo imediatamente antes de cada escrita:

  1. origem: GET no Graph (etag, tamanho, quickXor) igual ao do planejamento; senao ALTERADO_DURANTE_EXECUCAO;
  2. pasta de destino: existe no caminho previsto e com o MESMO oc:fileid registrado (pasta renomeada ou
     movida por terceiro -> PASTA_INVALIDA; o caminho antigo nunca e recriado);
  3. destino: ausente (se um terceiro criou -> CONFLITO_CONCORRENTE; se e nosso -> so confere);
  4. escrita condicional (PUT If-None-Match:* / chunking + MOVE Overwrite:F): 412 -> CONFLITO_CONCORRENTE;
  5. releitura integral do PARCEIRO: SHA-256 = o da leitura da origem e QuickXor = o da Microsoft -> VALIDADO.
Pastas: so as da EAP e as que contem itens elegiveis, uma a uma (MKCOL), nunca Eventograma.

Seguranca operacional: trava exclusiva de arquivo (um so executor), pausa por arquivo PAUSAR, monitoramento
periodico do PARCEIRO (foto 362) que PAUSA tudo se algum arquivo criado por nos desaparecer ou mudar.

Recursos (29/10/2026): o G2 e SECUNDARIO. O governador (migra_g2/governador.py) decide, antes de cada envio
novo, quantos workers podem rodar (0 = nenhum); com pressao de RAM/CPU/commit ou o projeto prioritario pesado,
nada novo comeca e os envios ativos terminam. O processo roda em BelowNormal + modo background. Pausado por
mais de `recursos.sair_apos_pausa_s`, sai com 3 (retomavel) e libera a memoria; a tarefa do Agendador relanca.

Uso: python scripts/367_g2_executar.py --rodovia RD-103 [--lote PILOTO --limite 12] [--workers 3]
Saida: 0 fila concluida | 3 pausado/interrompido (retomavel) | 1 bloqueado | 6 ja ha executor ativo
"""

from __future__ import annotations

import argparse
import ctypes
import json
import msvcrt
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from graph_util import Graph  # noqa: E402
from migra_g2.base import (abrir_execucao, agora, cfg, conectar, digital_codigo_escrita, escrita_permitida,  # noqa: E402
                           fechar_execucao, gib, num, pasta, saida)
from migra_g2.credencial import parceiro  # noqa: E402
from migra_g2.dav import EscritaProibida, Ocupado  # noqa: E402
from migra_g2.envio import conferir, enviar, retomada_e_nossa  # noqa: E402
from migra_g2.governador import PAUSAS, Governador, baixar_prioridade_propria  # noqa: E402
from migra_parceiro.motor import _mesmo_qxh  # noqa: E402
from migra_parceiro.retomavel import FonteGraph  # noqa: E402

MONITOR_S = 20 * 60
TICK_S = 15                                             # intervalo maximo entre avaliacoes do governador
REGISTRO_S = 60                                         # linha na tabela recurso, alem das mudancas de estado


def novo_governador(c: dict) -> Governador:
    return Governador(c.get("recursos"))


class Log:
    def __init__(self, caminho: Path):
        self.f = open(caminho, "a", encoding="utf-8")
        self.t = threading.Lock()

    def __call__(self, msg: str):
        with self.t:
            linha = f"{agora()} {msg}"
            print(linha, flush=True)
            self.f.write(linha + "\n")
            self.f.flush()


def trava_exclusiva(caminho: Path):
    """Trava de arquivo do Windows: liberada pelo SO se o processo morrer. None se outro executor a tem."""
    f = open(caminho, "a+")
    f.seek(0)
    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        f.close()
        return None
    f.seek(0)
    f.truncate()
    f.write(json.dumps({"pid": os.getpid(), "desde": agora()}))
    f.flush()
    return f


def selecionar_piloto(con, rodovia: str) -> int:
    """Marca o lote PILOTO DESTA rodovia (2 menores por categoria + 1 entre 70 e 200 MiB, para o chunking), so se
    ela ainda nao tiver piloto. Devolve quantos foram marcados. (Antes nao filtrava a rodovia: achado 1 da 5a
    revisao de QA - o piloto da proxima GO sairia vazio.)"""
    if con.execute("SELECT 1 FROM plano WHERE lote='PILOTO' AND rodovia=?", (rodovia,)).fetchone():
        return 0
    elegivel = ("p.rodovia=? AND p.classificacao='AUSENTE_E_ELEGIVEL' AND p.estado IN "
                "('PLANEJADO','NA_FILA','COPIANDO','COPIADO') AND p.lote IS NULL")
    cur = con.execute(f"""UPDATE plano SET lote='PILOTO' WHERE rowid IN (
        SELECT rowid FROM (SELECT p.rowid, ROW_NUMBER() OVER (PARTITION BY p.categoria ORDER BY o.tamanho) n
          FROM plano p JOIN origem_item o USING (drive_id, item_id) WHERE {elegivel} AND o.tamanho > 0) WHERE n <= 2
        UNION SELECT * FROM (SELECT p.rowid FROM plano p JOIN origem_item o USING (drive_id, item_id)
          WHERE {elegivel} AND o.tamanho BETWEEN 70*1048576 AND 200*1048576 ORDER BY o.tamanho LIMIT 1))""",
                      (rodovia, rodovia))
    con.commit()
    return cur.rowcount


def pastas_eap(con, rodovia: str, c: dict) -> list[str]:
    """Nos fixos da EAP da rodovia (sem placeholders [..]) fora de Eventograma, mais as subpastas fixas do
    Executivo da nota 02. Caminhos completos a partir da raiz WebDAV."""
    raiz = c["destino"]["arvores"][rodovia]
    base = raiz.rsplit("/", 1)[0]                       # PARCEIRO-INFRA/05. RD-103
    out = []
    for (cam,) in con.execute("SELECT caminho FROM eap_no WHERE rodovia=? ORDER BY nivel, caminho", (rodovia,)):
        full = f"PARCEIRO-INFRA/{cam}"
        if "[" in full or "09. Eventograma" in full or full == raiz:
            continue
        out.append(full)
    ex = f"{raiz}/01. Projetos/02. Executivo"
    out += [f"{ex}/{s}" for s in ("01. FASE DE ESTUDOS", "02. VIARIO PRINC", "03. VIARIO SEC", "04. OAE",
                                  "05. VOLUMES FINAIS")]
    assert all(p.startswith(base) for p in out)
    return out


class Executor:
    def __init__(self, a, c, log):
        self.a, self.c, self.log = a, c, log
        self.con = conectar()
        self.dbt = threading.Lock()
        self.nc = parceiro(c)
        self.g = Graph()
        self.tmp = pasta("tmp", c)
        self.pastas: dict[str, str] = {}               # caminho -> fileid conhecido
        self.trava_pastas = threading.RLock()          # MKCOL serializado: dois workers na mesma pasta dao 423
        self.parar = threading.Event()
        self.inicio_epoch = time.time()                # upload posterior ao inicio desta execucao nao e da anterior
        self.cont = {"VALIDADO": 0, "bytes": 0}

    # ------------------------------------------------------------ banco
    def sql(self, q, p=()):
        with self.dbt:
            cur = self.con.execute(q, p)
            self.con.commit()
            return cur

    def um(self, q, p=()):
        """Leitura de uma linha sob a trava: a conexao sqlite e compartilhada pelas threads."""
        with self.dbt:
            return self.con.execute(q, p).fetchone()

    def pausado(self) -> bool:
        return (self.tmp / "PAUSAR").exists() or self.parar.is_set()

    # ------------------------------------------------------------ recursos
    def governar(self, ativos: int = 0):
        """Pergunta ao governador quantos workers podem rodar; registra mudanca de estado e a cada minuto."""
        d = self.gov.avaliar((self.tmp / "PAUSAR").exists())
        agora_ = time.time()
        mudou = d.estado != getattr(self, "_ult_estado", None)
        if mudou:
            self.log(f"GOVERNADOR: {getattr(self, '_ult_estado', None) or 'inicio'} -> {d.estado} "
                     f"(workers {d.workers}) :: {d.motivo}")
            self._ult_estado = d.estado
        if mudou or agora_ - getattr(self, "_ult_reg", 0) >= REGISTRO_S:
            self._ult_reg = agora_
            l = d.leitura
            self.sql("INSERT INTO recurso (em, rodovia, estado, workers, ativos, motivo, ram_livre_mb, commit_pct,"
                     " cpu_pct, prioritario_proc, prioritario_ws_mb, disco_livre_gib, validados, bytes_validados)"
                     " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (agora(), self.a.rodovia, d.estado, d.workers, ativos, d.motivo, l.get("ram_livre_mb"),
                      l.get("commit_pct"), l.get("cpu_pct"), l.get("prioritario_proc"), l.get("prioritario_ws_mb"),
                      l.get("disco_livre_gib"), self.cont["VALIDADO"], self.cont["bytes"]))
            try:
                (self.tmp / "GOVERNADOR.json").write_text(json.dumps(
                    {"em": agora(), "rodovia": self.a.rodovia, "estado": d.estado, "workers": d.workers,
                     "ativos": ativos, "motivo": d.motivo, "leitura": l, "pid": os.getpid()},
                    ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
        return d

    def aguardar_recursos(self) -> bool:
        """Antes de qualquer trabalho: espera o governador liberar. False se a pausa passou do limite (sair)."""
        limite = int((self.c.get("recursos") or {}).get("sair_apos_pausa_s", 900))
        t0 = time.time()
        while True:
            d = self.governar()
            if d.workers > 0:
                return True
            if self.parar.is_set() or time.time() - t0 >= limite:
                self.log(f"sem recursos ociosos ha {int(time.time() - t0)} s ({d.estado}: {d.motivo}); saindo "
                         "para liberar memoria. Estado preservado; a tarefa do Agendador relanca.")
                return False
            time.sleep(TICK_S)

    # ------------------------------------------------------------ pastas
    def garantir_pasta(self, caminho: str) -> str | None:
        with self.trava_pastas:
            return self._garantir_pasta(caminho)

    def _garantir_pasta(self, caminho: str) -> str | None:
        """Cria (se preciso) e devolve o fileid. Confere a cadeia: cada pasta conhecida tem de manter o fileid."""
        if caminho in self.pastas:
            return self.pastas[caminho]
        pai = caminho.rsplit("/", 1)[0]
        if pai not in self.pastas and escrita_permitida(pai, True, self.c)[0]:
            if not self._garantir_pasta(pai):
                return None
        e = self.nc.estado(caminho)
        if e is None:
            ok, motivo = escrita_permitida(caminho, True, self.c)
            if not ok:
                self.log(f"PASTA RECUSADA pela trava: {caminho} ({motivo})")
                return None
            if pai in self.pastas:                     # o pai ainda e o mesmo (nao foi movido por terceiro)?
                ep = self.nc.estado(pai)
                if not ep or ep.fileid != self.pastas[pai]:
                    self.log(f"PASTA PAI MUDOU antes do MKCOL: {pai}")
                    return None
            r = self.nc.criar_pasta(caminho, self.c)
            e = self.nc.estado(caminho)
            if e is None:
                self.log(f"PASTA nao confirmada apos MKCOL: {caminho}")
                return None
            self.sql("INSERT OR REPLACE INTO pasta_criada (caminho, motivo, criada_em, fileid, resultado)"
                     " VALUES (?,?,?,?,?)", (caminho, "EAP/destino de item elegivel", agora(), e.fileid if e else None,
                                             "CRIADA" if r == "CRIADA" else "JA_EXISTIA"))
            if r == "CRIADA":
                self.log(f"pasta criada: {caminho}")
        elif not e.eh_pasta:
            self.log(f"CONFLITO: existe ARQUIVO onde a EAP preve pasta: {caminho}")
            return None
        else:
            self.sql("INSERT OR IGNORE INTO pasta_criada (caminho, motivo, fileid, resultado) VALUES (?,?,?,?)",
                     (caminho, "ja existia", e.fileid, "JA_EXISTIA"))
        self.pastas[caminho] = e.fileid
        return e.fileid

    # ------------------------------------------------------------ item
    def item(self, row) -> str:
        d, i, dest = row["drive_id"], row["item_id"], row["destino_rel"]
        t0, inicio = time.time(), agora()
        res, http, det, metodo, nbytes = "ERRO", None, "", None, 0
        self.sql("UPDATE plano SET estado='COPIANDO', tentativas=tentativas+1, atualizado_em=? WHERE drive_id=? AND"
                 " item_id=? AND destino_rel=?", (agora(), d, i, dest))
        try:
            it = self.g.get(f"/drives/{d}/items/{i}?$select=id,name,size,eTag,file,deleted")
            o = self.um("SELECT * FROM origem_item WHERE drive_id=? AND item_id=?", (d, i))
            qx = ((it.get("file") or {}).get("hashes") or {}).get("quickXorHash")
            if it.get("eTag") != row["etag_origem"] or it.get("size") != o["tamanho"] or qx != o["quick_xor_hash"]:
                res, det = "ORIGEM_ALTERADA", f"etag/tamanho/hash mudou desde o planejamento"
                self.marcar(row, "PULADO", "ALTERADO_DURANTE_EXECUCAO", det)
                return res
            pai = dest.rsplit("/", 1)[0]
            fid_pai = self.garantir_pasta(pai)
            ep = self.nc.estado(pai) if fid_pai else None
            if not fid_pai or not ep or ep.fileid != fid_pai:
                res, det = "PASTA_INVALIDA", "pasta de destino ausente, recusada ou com outro fileid"
                self.marcar(row, "PULADO", "CONFLITO_CONCORRENTE", det)
                return res
            e = self.nc.estado(dest)
            if e is not None:
                nosso = self.um("SELECT 1 FROM plano WHERE fileid_destino=?", (e.fileid,))
                retomada = retomada_e_nossa(row["estado"], row["sha256"], e.criado, row["atualizado_em"],
                                            limite_epoch=self.inicio_epoch)
                if not nosso and not retomada:
                    v = conferir(self.nc, dest, o["tamanho"], "-", o["quick_xor_hash"])
                    igual = v["qx_igual_microsoft"] and v["tamanho_lido"] == o["tamanho"]
                    det = (f"destino surgiu antes do envio (fileid {e.fileid}); "
                           + ("conteudo IDENTICO a origem" if igual else "conteudo DIFERENTE") + "; nao sobrescrito")
                    self.marcar(row, "PULADO", "JA_EXISTIA_CORRETO" if igual else "CONFLITO_CONCORRENTE", det)
                    return "JA_OCUPADO"
                # envio nosso de uma execucao interrompida: so vale se o conteudo bater com a origem (abaixo)
                r = {"sha256_origem": row["sha256"], "metodo": None, "bytes": 0}
                if not r["sha256_origem"]:
                    v = conferir(self.nc, dest, o["tamanho"], "-", o["quick_xor_hash"])
                    if v["qx_igual_microsoft"] and v["tamanho_lido"] == o["tamanho"]:
                        r["sha256_origem"] = v["sha256_destino"]      # QuickXor da Microsoft comprova a origem
                    else:
                        self.marcar(row, "PULADO", "CONFLITO_CONCORRENTE", "destino ocupado com conteudo diferente")
                        return "JA_OCUPADO"
            else:
                fonte = FonteGraph(self.g, d, i, o["tamanho"])
                r = enviar(self.nc, fonte, dest, self.c)
                metodo, nbytes = r["metodo"], r["bytes"]
                self.sql("UPDATE plano SET estado='COPIADO', sha256=?, copiado_em=? WHERE drive_id=? AND item_id=?"
                         " AND destino_rel=?", (r["sha256_origem"], agora(), d, i, dest))
            v = conferir(self.nc, dest, o["tamanho"], r["sha256_origem"], o["quick_xor_hash"]) if r["sha256_origem"] \
                else None
            if v and v["validado"]:
                self.sql("UPDATE plano SET estado='VALIDADO', fileid_destino=?, quick_xor_dest=?, sha256=?, validado_em=?,"
                         " erro=NULL, atualizado_em=? WHERE drive_id=? AND item_id=? AND destino_rel=?",
                         (v["fileid"], v["quick_xor_destino"], v["sha256_destino"], agora(), agora(), d, i, dest))
                res = "CRIADO" if metodo else "CONFERIDO"
                with self.dbt:
                    self.cont["VALIDADO"] += 1
                    self.cont["bytes"] += o["tamanho"] or 0
            else:
                res, det = "DIVERGENTE", json.dumps(v, ensure_ascii=False)[:500] if v else "sem hash de origem"
                self.sql("UPDATE plano SET estado='DIVERGENTE', fileid_destino=?, erro=?, atualizado_em=? WHERE"
                         " drive_id=? AND item_id=? AND destino_rel=?", (v["fileid"] if v else None, det, agora(), d, i, dest))
            return res
        except Ocupado:
            res, http, det = "JA_OCUPADO", 412, "servidor recusou: o destino passou a existir (corrida)"
            self.marcar(row, "PULADO", "CONFLITO_CONCORRENTE", det)
            return res
        except EscritaProibida as ex:
            res, det = "BLOQUEADO", str(ex)
            self.marcar(row, "PULADO", None, det)
            return res
        except Exception as ex:  # noqa: BLE001 (registra e segue: regra 5)
            det = f"{type(ex).__name__}: {str(ex)[:300]}"
            n = self.um("SELECT tentativas FROM plano WHERE drive_id=? AND item_id=? AND destino_rel=?",
                        (d, i, dest))[0]
            self.sql("UPDATE plano SET estado=?, erro=?, atualizado_em=? WHERE drive_id=? AND item_id=? AND destino_rel=?",
                     ("ERRO" if n >= int(self.c["transferencia"]["tentativas"]) else "NA_FILA", det, agora(), d, i, dest))
            return "ERRO"
        finally:
            self.sql("INSERT INTO envio (drive_id, item_id, destino_rel, lote, inicio, fim, metodo, http, resultado,"
                     " bytes, segundos, detalhe) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (d, i, dest, row["lote"], inicio, agora(), metodo, http, res, nbytes,
                      round(time.time() - t0, 2), det[:500]))

    def marcar(self, row, estado, classificacao, det):
        self.sql("UPDATE plano SET estado=?, classificacao=COALESCE(?, classificacao), erro=?, atualizado_em=? WHERE"
                 " drive_id=? AND item_id=? AND destino_rel=?",
                 (estado, classificacao, det, agora(), row["drive_id"], row["item_id"], row["destino_rel"]))
        self.log(f"PULADO [{classificacao or estado}] {row['destino_rel'][-120:]} :: {det}")

    # ------------------------------------------------------------ monitor
    def monitorar(self) -> bool:
        """Foto incremental do PARCEIRO; False (e pausa) se algum arquivo nosso sumiu ou mudou."""
        self.log("monitor: fotografando o PARCEIRO (362 --tipo monitor) ...")
        p = subprocess.run([sys.executable, str(RAIZ / "scripts" / "362_g2_foto_parceiro.py"), "--tipo", "monitor"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=RAIZ)
        self.log("monitor: " + (p.stdout.strip().splitlines() or ["(sem saida)"])[-1][:300])
        if getattr(p, "returncode", 0) != 0:           # foto falhou: nao da para afirmar que esta tudo bem
            self.log(f"ALERTA: a foto de monitoramento falhou (362 saiu com {p.returncode}). PAUSANDO.")
            (self.tmp / "PAUSAR").write_text(f"pausa automatica {agora()}: monitor sem foto valida\n", encoding="utf-8")
            return False
        foto = self.con.execute("SELECT MAX(id) FROM parceiro_foto WHERE fim IS NOT NULL").fetchone()[0]
        perdidos = self.con.execute(
            "SELECT p.destino_rel, p.fileid_destino FROM plano p WHERE p.estado='VALIDADO' AND p.fileid_destino IS NOT NULL"
            " AND p.validado_em < (SELECT inicio FROM parceiro_foto WHERE id=?) AND NOT EXISTS (SELECT 1 FROM parceiro_item i"
            " WHERE i.foto_id=? AND i.fileid=p.fileid_destino AND i.caminho=p.destino_rel)", (foto, foto)).fetchall()
        if perdidos:
            self.log(f"ALERTA: {len(perdidos)} arquivo(s) criados por nos sumiram/mudaram de lugar no PARCEIRO "
                     f"(ex.: {perdidos[0][0]}). PAUSANDO para decisao humana.")
            (self.tmp / "PAUSAR").write_text(f"pausa automatica {agora()}: arquivos nossos alterados por terceiros\n",
                                             encoding="utf-8")
            return False
        return True

    # ------------------------------------------------------------ laco
    def estado_piloto(self) -> str:
        """SEM_PILOTO | PENDENTE | OK | FALHOU, do lote PILOTO desta rodovia."""
        r = self.um("SELECT COUNT(*), SUM(estado='VALIDADO'), SUM(estado IN ('ERRO','DIVERGENTE')),"
                    " SUM(estado IN ('PLANEJADO','NA_FILA','COPIANDO','COPIADO')) FROM plano WHERE rodovia=? AND"
                    " lote='PILOTO'", (self.a.rodovia,))
        if not r[0]:
            return "SEM_PILOTO"
        if r[2]:
            return "FALHOU"
        if r[3]:
            return "PENDENTE"
        return "OK" if r[1] else "FALHOU"

    def rodar(self) -> int:
        """Em fase 'execucao' sem --lote, o piloto da rodovia vem antes, automaticamente, e a fila completa so
        roda se ele terminar sem ERRO/DIVERGENTE (pendencia M2 do HANDOFF_G2, resolvida em 29/10/2026)."""
        a = self.a
        if self.c["fase"] == "execucao" and not a.lote:
            if self.estado_piloto() in ("SEM_PILOTO", "PENDENTE"):
                a.lote = "PILOTO"
                self.log(f"{a.rodovia}: piloto obrigatorio antes da fila completa")
                r = self._rodar()
                a.lote = None
                if r != 0:
                    return r
            ep = self.estado_piloto()
            if ep != "OK":
                self.log(f"BLOQUEADO: piloto da {a.rodovia} {ep} (ver --falhas); a fila completa nao roda")
                return 1
            self.log(f"{a.rodovia}: piloto OK; seguindo para a fila completa")
        return self._rodar()

    def _rodar(self) -> int:
        a, c = self.a, self.c
        if not a.lote and not self.um("SELECT 1 FROM plano WHERE rodovia=? AND classificacao='AUSENTE_E_ELEGIVEL' AND"
                                      " estado IN ('PLANEJADO','NA_FILA','COPIANDO','COPIADO') LIMIT 1", (a.rodovia,)):
            self.log(f"{a.rodovia}: nada pendente na fila; nada a fazer")
            return 0
        if getattr(self, "gov", None) is None:
            self.gov = novo_governador(c)
        if not self.aguardar_recursos():               # nada de rede nem banco pesado antes de haver folga
            return 3
        raiz = c["destino"]["arvores"][a.rodovia]
        e = self.nc.estado(raiz)
        base = self.con.execute("SELECT fileid FROM parceiro_item WHERE foto_id=1 AND caminho=?", (raiz,)).fetchone()
        if not e or not base or e.fileid != base[0]:
            self.log(f"BLOQUEADO: a raiz {raiz} nao e mais a do baseline (fileid {base and base[0]} -> {e and e.fileid})")
            return 1
        self.pastas[raiz] = e.fileid
        for p in pastas_eap(self.con, a.rodovia, c):
            if self.pausado():
                return 3
            try:
                self.garantir_pasta(p)
            except Exception as ex_:  # noqa: BLE001  (registra e segue; os itens dessa pasta ficam PASTA_INVALIDA)
                self.log(f"PASTA DA EAP nao garantida: {p} :: {type(ex_).__name__}: {str(ex_)[:200]}")

        filtro = "rodovia=? AND classificacao='AUSENTE_E_ELEGIVEL' AND estado IN ('PLANEJADO','NA_FILA','COPIANDO','COPIADO')"
        par = [a.rodovia]
        if a.lote == "PILOTO":
            with self.dbt:
                n = selecionar_piloto(self.con, a.rodovia)
            if n:
                self.log(f"piloto selecionado para {a.rodovia}: {n} arquivos")
        if a.lote and a.lote != "PILOTO":
            self.sql(f"UPDATE plano SET lote=? WHERE rowid IN (SELECT rowid FROM plano WHERE {filtro} AND lote IS NULL"
                     " ORDER BY categoria, destino_rel LIMIT ?)" if a.limite else
                     f"UPDATE plano SET lote=? WHERE {filtro} AND lote IS NULL", [a.lote, *par] + ([a.limite] if a.limite else []))
        if a.lote:
            filtro += " AND lote=?"
            par.append(a.lote)
        fila = self.con.execute(f"SELECT * FROM plano WHERE {filtro} ORDER BY categoria, destino_rel", par).fetchall()
        tot = sum((self.con.execute("SELECT tamanho FROM origem_item WHERE drive_id=? AND item_id=?",
                                    (r["drive_id"], r["item_id"])).fetchone()[0] or 0) for r in fila)
        self.log(f"fila {a.lote or 'GERAL'}: {num(len(fila))} arquivos, {gib(tot)} GiB; workers {a.workers}")
        if a.lote == "PILOTO" and not fila:
            self.log(f"BLOQUEADO: lote PILOTO da {a.rodovia} sem arquivos pendentes (ja executado ou nada elegivel); "
                     "um piloto vazio nao conta como piloto")
            return 1
        ultimo_mon, t0 = time.time(), time.time()
        teto = max(1, min(int(a.workers), int(self.gov.p["workers_max"])))
        sair_pausa = int((c.get("recursos") or {}).get("sair_apos_pausa_s", 900))
        resultados: dict[str, int] = {}
        pend = list(fila)
        erro_ate = 0.0                                  # apos um ERRO, 1 worker por 10 min (rede/throttling)
        pausa_desde = None
        with ThreadPoolExecutor(max_workers=teto) as pool:
            ativos = set()
            while pend or ativos:
                d = self.governar(len(ativos))
                workers = min(teto, d.workers, 1 if time.time() < erro_ate else teto)
                if d.workers == 0:
                    pausa_desde = pausa_desde or time.time()
                    if not ativos and d.estado in PAUSAS and d.estado != "PAUSADO_MANUALMENTE" and \
                            time.time() - pausa_desde >= sair_pausa:
                        self.log(f"pausa por recursos ha {int(time.time() - pausa_desde)} s ({d.estado}); saindo para "
                                 "liberar memoria. Estado preservado; a tarefa do Agendador relanca.")
                        return 3
                else:
                    pausa_desde = None
                # monitor vencido: nao inicia envio novo; drena os ativos e fotografa (achado 1 da 6a revisao de QA:
                # com workers >= 2 a condicao "sem ativos" quase nunca ocorria e o monitor nao rodava). A foto
                # tambem e trabalho: so com o governador liberando.
                drenar = time.time() - ultimo_mon > MONITOR_S
                if drenar and not ativos and d.workers > 0:
                    ultimo_mon = time.time()
                    if not self.monitorar():
                        self.parar.set()
                        break
                    drenar = False
                while pend and len(ativos) < workers and not self.pausado() and not drenar:
                    ativos.add(pool.submit(self.item, pend.pop(0)))
                if not ativos:
                    if self.pausado() or not pend:
                        break
                    time.sleep(TICK_S)                  # sem envio ativo: espera o governador liberar
                    continue
                feitos, ativos = wait(ativos, timeout=TICK_S, return_when=FIRST_COMPLETED)
                for f in feitos:
                    r = f.result()
                    resultados[r] = resultados.get(r, 0) + 1
                    if r == "ERRO":
                        erro_ate = time.time() + 600
                if feitos and sum(resultados.values()) % 25 == 0:
                    dec = time.time() - t0
                    self.log(f"progresso: {num(sum(resultados.values()))}/{num(len(fila))} {resultados} | "
                             f"{gib(self.cont['bytes'])} GiB validados | {self.cont['bytes'] / 2**20 / max(dec, 1):.1f}"
                             f" MiB/s | {d.estado} workers {workers} | RAM livre {d.leitura.get('ram_livre_mb')} MB")
                if self.pausado() and not ativos:
                    break
        self.log(f"fim do ciclo: {resultados}; validados nesta execucao {num(self.cont['VALIDADO'])} "
                 f"({gib(self.cont['bytes'])} GiB)")
        if self.pausado():
            return 3
        if not self.aguardar_recursos():               # a foto final tambem cede ao projeto prioritario
            return 3
        if not self.monitorar():                       # monitor final falhou ou achou perda: nao e "concluido"
            return 3
        resta = self.con.execute(f"SELECT COUNT(*) FROM plano WHERE {filtro.replace(' AND lote=?', '')}",
                                 [a.rodovia]).fetchone()[0]
        return 0 if not resta or a.lote else 3


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rodovia", required=True)
    ap.add_argument("--lote")
    ap.add_argument("--limite", type=int)
    ap.add_argument("--workers", type=int)
    a = ap.parse_args()
    c = cfg()
    a.workers = a.workers or int(c["transferencia"]["concorrencia"])
    log = Log(pasta("logs", c) / "LOG_OPERACIONAL_G2.log")
    if c["fase"] not in ("piloto", "execucao"):
        log(f"BLOQUEADO: fase {c['fase']!r} nao permite escrita")
        return 1
    if a.rodovia not in (c["destino"].get("rodovias_liberadas") or []):
        log(f"BLOQUEADO: {a.rodovia} nao esta em rodovias_liberadas")
        return 1
    if c["fase"] == "piloto" and a.lote != "PILOTO":
        log("BLOQUEADO: na fase 'piloto' so roda --lote PILOTO (a fila completa exige fase 'execucao')")
        return 1
    testes = sorted(saida(c).glob("TESTE_MOTOR_*.json"))
    ult = json.loads(testes[-1].read_text(encoding="utf-8")) if testes else {}
    if not ult.get("todos_passaram"):
        log("BLOQUEADO: o ultimo teste do motor (365) nao passou")
        return 1
    if ult.get("codigo_escrita_sha256") != digital_codigo_escrita():
        log("BLOQUEADO: o ultimo teste do motor (365) foi feito com OUTRO codigo de escrita (base/dav/envio). "
            "Rode o 365 de novo (fase 'teste') antes de qualquer piloto ou producao.")
        return 1
    trava = trava_exclusiva(pasta("tmp", c) / "EXECUTOR.lock")
    if not trava:
        log("ja ha um executor G2 ativo (trava ocupada); saindo")
        return 6
    log(f"prioridade do proprio processo (pid {os.getpid()}): {baixar_prioridade_propria()}")
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)   # nao dormir durante a execucao
    ex_ = Executor(a, c, log)
    exid = abrir_execucao(ex_.con, "367_g2_executar", {k: v for k, v in vars(a).items()})
    try:
        r = ex_.rodar()
    finally:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
    fechar_execucao(ex_.con, exid, {0: "concluida", 3: "interrompida"}.get(r, "erro"), ex_.cont)
    return r


if __name__ == "__main__":
    raise SystemExit(main())

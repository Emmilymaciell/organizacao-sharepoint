"""
motor - fila persistente, copia e conferencia da migracao.

Estados (plano_item.estado):
  APROVADO -> EM_TRANSFERENCIA -> COPIADO -> EM_VERIFICACAO -> VALIDADO
  EM_TRANSFERENCIA -> FALHOU (retentavel) | CONFLITO (nao sobrescreve) | ORIGEM_ALTERADA
  EM_VERIFICACAO   -> DIVERGENTE | ORIGEM_ALTERADA
Idempotente: rodar de novo so pega APROVADO e FALHOU com tentativas sobrando;
EM_TRANSFERENCIA orfao (queda) volta para a fila e, se o arquivo ja estiver no
destino, vai direto para a conferencia - nunca e reenviado por cima.

Garantias de nao-destruicao:
- a origem so e lida (rc.py nao conhece metodo que escreva fora do destino);
- antes de copiar, o destino e consultado: arquivo que ja existe e nao foi
  criado por este motor NAO e sobrescrito: e conferido por hash; igual vira
  VALIDADO (JA_EXISTIA), diferente vira CONFLITO;
- a copia usa Immutable=true: se algo aparecer no caminho no meio do caminho,
  o rclone falha em vez de sobrescrever.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import threading
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass, field
from pathlib import Path

from .base import agora
from .espaco import Guarda
from .quickxor import QuickXorHash
from .rc import ErroRc, Rcd

BLOCO_LEITURA = 8 * 2**20


# ----------------------------------------------------------------------
# Trava de execucao unica
# ----------------------------------------------------------------------

def _pid_vivo(pid: int) -> bool:
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not h:
            return False
        codigo = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(codigo))
        ctypes.windll.kernel32.CloseHandle(h)
        return codigo.value == 259  # STILL_ACTIVE
    except Exception:
        return True   # na duvida, considera vivo (mais seguro)


class Trava:
    """Impede duas execucoes simultaneas sobre a mesma fila.

    Uma thread propria, com conexao sqlite PROPRIA, renova a batida a cada 60 s enquanto a
    execucao vive. Uma trava so e considerada abandonada se o processo dono morreu ou se a
    batida parou ha mais de 10 min (processo travado)."""

    INTERVALO = 60

    def __init__(self, con: sqlite3.Connection, nome: str = "fila"):
        self.con, self.nome = con, nome
        self._parar = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self):
        r = self.con.execute("SELECT * FROM trava WHERE nome=?", (self.nome,)).fetchone()
        if r and r["pid"] != os.getpid():
            parada = time.time() - r["batida"] > 600
            if r["host"] == socket.gethostname() and _pid_vivo(r["pid"]) and not parada:
                raise RuntimeError(f"ja ha uma execucao ativa (pid {r['pid']} desde {r['desde']})")
        self.con.execute("INSERT OR REPLACE INTO trava (nome, pid, host, desde, batida) VALUES (?,?,?,?,?)",
                         (self.nome, os.getpid(), socket.gethostname(), agora(), time.time()))
        self.con.commit()
        caminho = self.con.execute("PRAGMA database_list").fetchone()[2]
        self._thread = threading.Thread(target=self._batimento, args=(caminho,), daemon=True)
        self._thread.start()
        return self

    def _batimento(self, caminho: str):
        c = sqlite3.connect(caminho, timeout=60)
        try:
            while not self._parar.wait(self.INTERVALO):
                c.execute("UPDATE trava SET batida=? WHERE nome=? AND pid=?",
                          (time.time(), self.nome, os.getpid()))
                c.commit()
        finally:
            c.close()

    def __exit__(self, *_):
        self._parar.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.con.execute("DELETE FROM trava WHERE nome=? AND pid=?", (self.nome, os.getpid()))
        self.con.commit()


# ----------------------------------------------------------------------
# Classificacao de erros e saude da conexao
# ----------------------------------------------------------------------

import re as _re

# Condicoes GLOBAIS (valem para todos os arquivos): param o lote inteiro, sem gastar a fila.
_PARADA_GLOBAL = _re.compile(
    r"\b401\b|unauthorized|notauthenticated|username or password was incorrect|invalid_grant|"
    r"invalidauthenticationtoken|aadsts\d+|token (has )?expired|\b507\b|insufficient storage|quota")
_TRANSITORIOS = _re.compile(
    r"timeout|timed out|deadline exceeded|connection (reset|refused|aborted)|no such host|broken pipe|"
    r"\beof\b|unexpected eof|\b429\b|too many requests|\b5(00|02|03|04)\b|internal server error|"
    r"service unavailable|bad gateway|\b423\b|locked|temporar|tls handshake|network is unreachable|"
    # bloco do upload do Nextcloud que chegou vazio: o proprio servidor diz que pode ser rede (20/10/2026,
    # 3 casos noturnos classificados por engano como permanentes por causa do "400")
    r"uploading chunk failed|could either be a network problem|wrote \(to nextcloud storage\) 0 bytes")
_PERMANENTES = _re.compile(
    r"\b40[0345]\b|\b41[0-5]\b|forbidden|permission|not allowed|immutable|invalid (name|character|path)|"
    r"name too long|file name|not found|origem nao encontrada|unsupported|entity too large|bloqueado")


def classificar_erro(e: Exception, ignorar: tuple[str, ...] = ()) -> str:
    """'parada_global' (credencial/cota: para o lote), 'transitorio' (rede/limite: tenta de novo)
    ou 'permanente' (permissao/nome/conflito: isola o arquivo em EXCECAO).

    `ignorar`: caminhos e nomes do arquivo, retirados da mensagem antes de classificar, para que
    um 'Oficio 404.pdf' nao seja lido como HTTP 404."""
    # A API do rcd responde HTTP 500 para QUALQUER falha de operacao: esse status e da API, nao do
    # PARCEIRO/SharePoint. Classifica-se so a mensagem do rclone (que traz o status real, ex.: 403).
    s = e.mensagem if isinstance(e, ErroRc) else str(e)
    for trecho in ignorar:
        if trecho:
            s = s.replace(trecho, " ")
    s = s.lower()
    if _PARADA_GLOBAL.search(s):
        return "parada_global"
    if _TRANSITORIOS.search(s):
        return "transitorio"
    if _PERMANENTES.search(s):
        return "permanente"
    return "transitorio"


class Saude:
    """Conta falhas transitorias seguidas: rede caiu -> o motor espera em vez de queimar a fila."""

    def __init__(self):
        self.seguidas = 0
        self.transitorias_janela = 0
        self.parar_por_autenticacao = False
        self.trava = threading.Lock()

    def sucesso(self):
        with self.trava:
            self.seguidas = 0

    def falha_transitoria(self):
        with self.trava:
            self.seguidas += 1
            self.transitorias_janela += 1

    def zerar_janela(self) -> int:
        with self.trava:
            n, self.transitorias_janela = self.transitorias_janela, 0
            return n


class Concorrencia:
    """Paralelismo adaptativo (subida gradual / descida rapida), dentro de [minimo, maximo].

    A cada janela: erro transitorio ou memoria acima do teto -> reduz pela metade; vazao melhor
    que a janela anterior (>5%) -> continua no mesmo sentido; pior (<-5%) -> inverte; senao, mantem.
    """

    def __init__(self, inicial: int, minimo: int, maximo: int, janela_s: float = 90):
        self.alvo, self.minimo, self.maximo = max(minimo, min(inicial, maximo)), minimo, maximo
        self.janela_s, self.sentido = janela_s, +1
        self.t0, self.b0, self.vazao_ant = time.time(), 0, None
        self.historico: list[tuple[str, int, float, str]] = []

    def avaliar(self, bytes_acum: int, erros: int, memoria_alta: bool) -> int | None:
        agora_t = time.time()
        if agora_t - self.t0 < self.janela_s:
            return None
        vazao = (bytes_acum - self.b0) / max(agora_t - self.t0, 1e-3)
        antes = self.alvo
        if erros or memoria_alta:
            self.alvo, self.sentido = max(self.minimo, self.alvo // 2), +1
            motivo = "erros" if erros else "memoria"
        elif self.vazao_ant is None or vazao > self.vazao_ant * 1.05:
            self.alvo = max(self.minimo, min(self.maximo, self.alvo + self.sentido))
            motivo = "vazao subiu"
        elif vazao < self.vazao_ant * 0.95:
            self.sentido = -self.sentido
            self.alvo = max(self.minimo, min(self.maximo, self.alvo + self.sentido))
            motivo = "vazao caiu"
        else:
            motivo = "estavel"
        self.historico.append((agora(), antes, round(vazao / 2**20, 2), motivo))
        self.t0, self.b0, self.vazao_ant = agora_t, bytes_acum, vazao
        return self.alvo


# ----------------------------------------------------------------------
# Leitura com hash (conferencia)
# ----------------------------------------------------------------------

def ler_e_hashear(rcd: Rcd, fs: str, remoto: str) -> tuple[int, str, str]:
    """(bytes, quickxor_base64, sha256_hex) dos bytes efetivamente lidos de fs/remoto."""
    qx, sha, n = QuickXorHash(), hashlib.sha256(), 0
    with rcd.abrir_leitura(fs, remoto) as resp:
        while True:
            bloco = resp.read(BLOCO_LEITURA)
            if not bloco:
                break
            qx.update(bloco)
            sha.update(bloco)
            n += len(bloco)
    return n, qx.base64(), sha.hexdigest()


# ----------------------------------------------------------------------
# Motor
# ----------------------------------------------------------------------

@dataclass
class Progresso:
    total_arq: int = 0
    total_bytes: int = 0
    feitos_arq: int = 0
    feitos_bytes: int = 0
    falhas: int = 0
    conflitos: int = 0
    excecoes: int = 0
    ja_existiam: int = 0
    validados: int = 0
    em_voo: dict = field(default_factory=dict)
    inicio: float = field(default_factory=time.time)
    amostras: list = field(default_factory=list)   # (t, bytes acumulados)
    trava: threading.Lock = field(default_factory=threading.Lock)

    def instantaneo(self, guarda: Guarda | None) -> dict:
        agora_t = time.time()
        self.amostras = [a for a in self.amostras if agora_t - a[0] <= 60] + [(agora_t, self.feitos_bytes)]
        janela = self.amostras[-1][0] - self.amostras[0][0]
        vel_atual = (self.amostras[-1][1] - self.amostras[0][1]) / janela if janela > 0 else 0
        decorrido = agora_t - self.inicio
        vel_media = self.feitos_bytes / decorrido if decorrido > 0 else 0
        restante = self.total_bytes - self.feitos_bytes
        d = {"atualizado_em": agora(), "decorrido_s": round(decorrido),
             "arquivos": {"total": self.total_arq, "feitos": self.feitos_arq,
                          "restantes": self.total_arq - self.feitos_arq, "falhas": self.falhas,
                          "conflitos": self.conflitos, "excecoes": self.excecoes,
                          "ja_existiam": self.ja_existiam, "validados_em_linha": self.validados},
             "bytes": {"total": self.total_bytes, "feitos": self.feitos_bytes, "restantes": restante},
             "pct_arquivos": round(100 * self.feitos_arq / self.total_arq, 2) if self.total_arq else 100,
             "pct_bytes": round(100 * self.feitos_bytes / self.total_bytes, 2) if self.total_bytes else 100,
             "vel_atual_MBps": round(vel_atual / 2**20, 2), "vel_media_MBps": round(vel_media / 2**20, 2),
             "eta_s": round(restante / vel_media) if vel_media > 0 else None,
             "em_voo": list(self.em_voo.values())}
        if guarda:
            d["disco_livre_GiB"] = round(guarda.livre() / 2**30, 2)
        return d


class Motor:
    def __init__(self, con: sqlite3.Connection, rcd: Rcd, src_fs: str, dst_fs: str, *,
                 concorrencia: int = 4, max_tentativas: int = 4, guarda: Guarda | None = None,
                 arquivo_progresso: Path | None = None, arquivo_pausa: Path | None = None,
                 execucao_id: int | None = None, log=print, concorrencia_min: int | None = None,
                 concorrencia_max: int | None = None, tentativas_por_arquivo: int = 3,
                 memoria_max_mb: int = 0, janela_ajuste_s: float = 90, conferir_em_linha: bool = False):
        # conferir_em_linha: cada arquivo e relido do PARCEIRO logo depois de copiado, na mesma thread.
        # A conferencia fica progressiva (nao acumula para o fim) e o VALIDADO sai junto da copia.
        self.conferir_em_linha = conferir_em_linha
        self.con, self.rcd = con, rcd
        self.src_fs, self.dst_fs = src_fs, dst_fs
        self.concorrencia, self.max_tentativas = concorrencia, max_tentativas
        self.ctrl = Concorrencia(concorrencia, concorrencia_min or concorrencia,
                                 concorrencia_max or concorrencia, janela_ajuste_s)
        self.tentativas_por_arquivo = max(1, tentativas_por_arquivo)
        self.memoria_max = memoria_max_mb * 2**20
        self.saude = Saude()
        self.guarda = guarda
        self.arquivo_progresso, self.arquivo_pausa = arquivo_progresso, arquivo_pausa
        self.ex_id, self.log = execucao_id, log
        self.db = threading.Lock()
        self.prog = Progresso()

    # -- banco -------------------------------------------------------------
    def _mudar(self, item_id: str, de: str | None, para: str, detalhe: str = "", **campos):
        with self.db:
            sets = ", ".join(["estado=?", "atualizado_em=?"] + [f"{k}=?" for k in campos])
            self.con.execute(f"UPDATE plano_item SET {sets} WHERE item_id=?",
                             [para, agora(), *campos.values(), item_id])
            self.con.execute("INSERT INTO evento (item_id, quando, de_estado, para_estado, detalhe, execucao_id) "
                             "VALUES (?,?,?,?,?,?)", (item_id, agora(), de, para, detalhe[:500], self.ex_id))
            self.con.commit()

    def _tentativas(self, item_id: str) -> int:
        """So tentativas de copia que FALHARAM contam para o limite. Parada global (401, cota),
        CONFLITO, JA_EXISTIA e COPIADO nao gastam tentativas."""
        with self.db:
            return self.con.execute("SELECT COUNT(*) FROM transferencia WHERE item_id=? AND resultado='FALHOU'",
                                    (item_id,)).fetchone()[0]

    def _numero_tentativa(self, item_id: str) -> int:
        with self.db:
            return self.con.execute("SELECT COUNT(*) FROM transferencia WHERE item_id=?", (item_id,)).fetchone()[0] + 1

    @staticmethod
    def _trechos(it, destino: str) -> tuple[str, ...]:
        return (it["caminho_rel"], destino, it["nome_final"], it["caminho_rel"].rsplit("/", 1)[-1])

    def _registrar(self, item_id, lote, tentativa, inicio, bytes_, resultado, erro=""):
        """inicio = time.time() do comeco da tentativa (gravado como data/hora real)."""
        from datetime import datetime
        from .base import BRT
        with self.db:
            self.con.execute("INSERT INTO transferencia (item_id, lote, tentativa, inicio, fim, bytes, segundos, "
                             "resultado, erro, execucao_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (item_id, lote, tentativa,
                              datetime.fromtimestamp(inicio, BRT).isoformat(timespec="seconds"), agora(),
                              bytes_, round(time.time() - inicio, 2), resultado, erro[:500], self.ex_id))
            self.con.commit()

    def _registrar_verif(self, item_id, destino, n, qx, sha, sha_o, metodo, resultado, detalhe=""):
        with self.db:
            self.con.execute("INSERT INTO verificacao (item_id, quando, caminho_destino, bytes_destino, qxh_destino, "
                             "sha256_destino, sha256_origem, metodo, resultado, detalhe, execucao_id) "
                             "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                             (item_id, agora(), destino, n, qx, sha, sha_o, metodo, resultado, detalhe[:500],
                              self.ex_id))
            self.con.commit()

    def _itens(self, lote: str, estados: tuple[str, ...]) -> list[sqlite3.Row]:
        marc = ",".join("?" * len(estados))
        return self.con.execute(
            f"SELECT p.item_id, p.estado, p.destino_pasta, p.nome_final, o.caminho_rel, o.tamanho, "
            f"o.quick_xor_hash FROM plano_item p JOIN origem_item o USING(item_id) "
            f"WHERE p.lote=? AND p.estado IN ({marc}) ORDER BY o.tamanho", (lote, *estados)).fetchall()

    # -- uma copia ---------------------------------------------------------
    def _copiar_um(self, it: sqlite3.Row, lote: str) -> str:
        destino = f"{it['destino_pasta']}/{it['nome_final']}"
        tentativa = self._numero_tentativa(it["item_id"])
        inicio = time.time()
        self._mudar(it["item_id"], it["estado"], "EM_TRANSFERENCIA", f"tentativa {tentativa}")
        try:
            existente = self.rcd.stat(self.dst_fs, destino)
            if existente:
                # Quem criou nao importa (outra pessoa, ou tentativa anterior interrompida):
                # decide-se pelo conteudo, e nada e sobrescrito. Um CONFLITO logo apos uma
                # retomada provavelmente e envio parcial do proprio motor: triagem manual.
                n, qx, sha = ler_e_hashear(self.rcd, self.dst_fs, destino)
                if it["quick_xor_hash"]:
                    igual = _mesmo_qxh(qx, it["quick_xor_hash"]) and n == it["tamanho"]
                else:
                    n_o, _q, sha_o = ler_e_hashear(self.rcd, self.src_fs, it["caminho_rel"])
                    igual = n == n_o and sha == sha_o
                if igual:
                    self._registrar(it["item_id"], lote, tentativa, inicio, 0, "JA_EXISTIA", "identico por QuickXor")
                    self._mudar(it["item_id"], "EM_TRANSFERENCIA", "COPIADO",
                                "ja existia no PARCEIRO com o mesmo conteudo; nao reenviado")
                    return "JA_EXISTIA"
                motivo = f"ja existe no PARCEIRO com outro conteudo ({n} B); nao sobrescrito"
                self._registrar(it["item_id"], lote, tentativa, inicio, 0, "CONFLITO", motivo)
                self._mudar(it["item_id"], "EM_TRANSFERENCIA", "CONFLITO", motivo, conflito=motivo)
                return "CONFLITO"

            origem = self.rcd.stat(self.src_fs, it["caminho_rel"], hash_=True)
            qx_orig = (((origem or {}).get("Hashes") or {}).get("quickxor") or "")
            if not origem or origem.get("Size") != it["tamanho"] or (
                    it["quick_xor_hash"] and qx_orig and not _mesmo_qxh(qx_orig, it["quick_xor_hash"])):
                motivo = ("origem nao encontrada" if not origem else "origem alterada") + \
                         " desde o inventario; reinventariar antes de copiar"
                self._registrar(it["item_id"], lote, tentativa, inicio, 0, "ORIGEM_ALTERADA", motivo)
                self._mudar(it["item_id"], "EM_TRANSFERENCIA", "ORIGEM_ALTERADA", motivo)
                return "ORIGEM_ALTERADA"

        except Exception as e:
            return self._falha(it, lote, tentativa, inicio, e, destino)
        return self._enviar(it, lote, destino, tentativa, inicio)

    def _enviar(self, it, lote, destino, tentativa, inicio) -> str:
        # copia, com retentativa imediata (backoff) so para erro TRANSITORIO
        for k in range(self.tentativas_por_arquivo):
            if k:
                tentativa = self._numero_tentativa(it["item_id"])
                inicio = time.time()
            try:
                self.rcd.copiar(self.src_fs, it["caminho_rel"], self.dst_fs, destino, {"Immutable": True})
                self._registrar(it["item_id"], lote, tentativa, inicio, it["tamanho"], "COPIADO")
                self._mudar(it["item_id"], "EM_TRANSFERENCIA", "COPIADO", f"{time.time() - inicio:.1f} s")
                self.saude.sucesso()
                return "COPIADO"
            except Exception as e:
                tipo = classificar_erro(e, self._trechos(it, destino))
                if tipo != "transitorio" or k == self.tentativas_por_arquivo - 1 or self.saude.seguidas >= 6:
                    return self._falha(it, lote, tentativa, inicio, e, destino)
                self._registrar(it["item_id"], lote, tentativa, inicio, 0, "FALHOU", f"transitorio: {e}")
                self.saude.falha_transitoria()
                time.sleep(min(5 * 2 ** k, 300))
        return "FALHOU"

    def _falha(self, it, lote, tentativa, inicio, e, destino: str = "") -> str:
        """Transitorio -> FALHOU (volta para a fila). Permissao/nome/conflito -> EXCECAO (isolado).
        Credencial/cota (parada global) -> o item volta para APROVADO, sem gastar tentativa, e o lote para."""
        erro, tipo = str(e), classificar_erro(e, self._trechos(it, destino))
        if tipo == "parada_global":
            self.saude.parar_por_autenticacao = True
            self.saude.motivo_parada = erro[:300]
            self._registrar(it["item_id"], lote, tentativa, inicio, 0, "PARADA_GLOBAL", erro)
            self._mudar(it["item_id"], "EM_TRANSFERENCIA", "APROVADO", f"parada global: {erro}")
            return "PARADA_GLOBAL"
        if tipo == "permanente":
            self._registrar(it["item_id"], lote, tentativa, inicio, 0, "EXCECAO", erro)
            self._mudar(it["item_id"], "EM_TRANSFERENCIA", "EXCECAO", f"{tipo}: {erro}")
            return "EXCECAO"
        self._registrar(it["item_id"], lote, tentativa, inicio, 0, "FALHOU", erro)
        self._mudar(it["item_id"], "EM_TRANSFERENCIA", "FALHOU", erro)
        self.saude.falha_transitoria()
        return "FALHOU"

    # -- lote ----------------------------------------------------------------
    def transferir(self, lote: str) -> dict:
        # orfaos de uma queda voltam para a fila
        orfaos = self._itens(lote, ("EM_TRANSFERENCIA",))
        for it in orfaos:
            self._mudar(it["item_id"], "EM_TRANSFERENCIA", "APROVADO", "retomada apos interrupcao")
        # FALHOU que ja gastou todas as tentativas ganha estado proprio (aparece no 338/excecoes)
        for it in self._itens(lote, ("FALHOU",)):
            if self._tentativas(it["item_id"]) >= self.max_tentativas:
                self._mudar(it["item_id"], "FALHOU", "ESGOTADO", f"{self.max_tentativas} tentativas sem sucesso")
        fila = deque(self._itens(lote, ("APROVADO", "FALHOU")))
        p = self.prog
        p.total_arq, p.total_bytes = len(fila), sum(it["tamanho"] or 0 for it in fila)
        self.log(f"Lote {lote}: {len(fila)} arquivos na fila ({len(orfaos)} retomados); "
                 f"concorrencia {self.ctrl.alvo} [{self.ctrl.minimo}-{self.ctrl.maximo}]")
        pausado = parado_disco = parado_auth = False
        esperas_rede = 0          # backoff de rede: cresce com o TEMPO de queda, nao com itens em voo
        em_prova = False          # apos uma espera, so 1 arquivo testa a rede antes de liberar o resto
        with ThreadPoolExecutor(max_workers=self.ctrl.maximo) as pool:
            pendentes = set()
            ultimo_progresso = 0.0
            while True:
                mem_alta = bool(self.memoria_max) and self._memoria_rclone() > self.memoria_max
                novo = self.ctrl.avaliar(p.feitos_bytes, self.saude.zerar_janela() if
                                         time.time() - self.ctrl.t0 >= self.ctrl.janela_s else 0, mem_alta)
                if novo is not None:
                    self.log(f"concorrencia -> {novo} ({self.ctrl.historico[-1][3]}, "
                             f"{self.ctrl.historico[-1][2]} MiB/s na janela)")
                parado_auth = parado_auth or self.saude.parar_por_autenticacao
                if self.arquivo_pausa and self.arquivo_pausa.exists() and not pausado:
                    pausado = True
                    self.log("PAUSAR encontrado: termina os arquivos em voo e nao inicia novos")
                if self.saude.seguidas == 0:
                    esperas_rede, em_prova = 0, False
                rede_caida = self.saude.seguidas >= 6
                if rede_caida and not pendentes and fila and not (pausado or parado_disco or parado_auth):
                    espera = min(30 * 2 ** esperas_rede, 900)
                    esperas_rede += 1
                    self.log(f"rede instavel ({self.saude.seguidas} falhas seguidas): aguardando {espera} s "
                             f"antes de testar com 1 arquivo")
                    self._esperar(espera)
                    with self.saude.trava:
                        self.saude.seguidas = 5
                    em_prova, rede_caida = True, False
                limite = 1 if em_prova else self.ctrl.alvo
                while len(pendentes) < limite and fila and not (pausado or parado_disco or parado_auth or rede_caida):
                    if self.guarda:
                        ok, motivo = self.guarda.pode_iniciar(0)
                        if not ok:
                            parado_disco = True
                            self.log(f"PARADO por espaco em disco: {motivo}")
                            break
                    it = fila.popleft()
                    with p.trava:
                        p.em_voo[it["item_id"]] = it["caminho_rel"]
                    fut = pool.submit(self._copiar_e_conferir, it, lote)
                    fut.item = it
                    pendentes.add(fut)
                if not pendentes:
                    if fila and rede_caida and not (pausado or parado_disco or parado_auth):
                        continue          # volta ao topo para a espera de rede
                    break
                # espera no maximo 5 s: pausa, disco, memoria e batimento seguem sendo avaliados
                prontos, _ = wait(pendentes, timeout=5, return_when=FIRST_COMPLETED)
                for feito in prontos:
                    pendentes.discard(feito)
                    it, res = feito.item, feito.result()
                    with p.trava:
                        p.em_voo.pop(it["item_id"], None)
                        if res in ("COPIADO", "JA_EXISTIA"):
                            p.feitos_arq += 1
                            p.feitos_bytes += it["tamanho"] or 0
                            p.ja_existiam += res == "JA_EXISTIA"
                        elif res == "CONFLITO":
                            p.conflitos += 1
                        elif res == "EXCECAO":
                            p.excecoes += 1
                        elif res == "ORIGEM_ALTERADA":
                            p.excecoes += 1
                        elif res == "FALHOU":
                            # transitorio: volta para o fim da fila nesta mesma execucao, se ainda tem tentativa
                            if self._tentativas(it["item_id"]) < self.max_tentativas:
                                fila.append(self._recarregar(it["item_id"]))
                            else:
                                self._mudar(it["item_id"], "FALHOU", "ESGOTADO",
                                            f"{self.max_tentativas} tentativas sem sucesso")
                                p.falhas += 1
                if self.arquivo_progresso and time.time() - ultimo_progresso > 5:
                    self._gravar_progresso(lote)
                    ultimo_progresso = time.time()
        if parado_auth:
            self.log("PARADO (condicao global): " + getattr(self.saude, "motivo_parada", "") +
                     " | Nada mais foi enviado; os arquivos voltaram para APROVADO sem gastar tentativa.")
        self._gravar_progresso(lote)
        return {"lote": lote, "pausado": pausado, "parado_por_disco": parado_disco,
                "parado_por_autenticacao": parado_auth,
                **self.prog.instantaneo(self.guarda)["arquivos"],
                "bytes_feitos": self.prog.feitos_bytes,
                "ajustes_concorrencia": self.ctrl.historico}

    def _copiar_e_conferir(self, it, lote: str) -> str:
        res = self._copiar_um(it, lote)
        if self.conferir_em_linha and res in ("COPIADO", "JA_EXISTIA"):
            linha = dict(self._recarregar(it["item_id"]))
            self.ultima_conferencia = self.conferir_um(linha)   # VALIDADO / DIVERGENTE / ...
            if self.ultima_conferencia == "VALIDADO":
                with self.prog.trava:
                    self.prog.validados += 1
        return res

    def _esperar(self, segundos: float) -> None:
        """Espera de rede interrompivel pelo arquivo PAUSAR (checado a cada 5 s)."""
        fim = time.time() + segundos
        while time.time() < fim and not (self.arquivo_pausa and self.arquivo_pausa.exists()):
            time.sleep(min(5, max(0, fim - time.time())))

    def _recarregar(self, item_id: str) -> sqlite3.Row:
        with self.db:
            return self.con.execute(
                "SELECT p.item_id, p.estado, p.destino_pasta, p.nome_final, o.caminho_rel, o.tamanho, "
                "o.quick_xor_hash FROM plano_item p JOIN origem_item o USING(item_id) WHERE p.item_id=?",
                (item_id,)).fetchone()

    def _memoria_rclone(self) -> int:
        try:
            return int(self.rcd.chamar("core/memstats", {}).get("Sys", 0))
        except Exception:
            return 0

    def _gravar_progresso(self, lote: str):
        if not self.arquivo_progresso:
            return
        d = self.prog.instantaneo(self.guarda)
        d["lote"] = lote
        tmp = self.arquivo_progresso.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.arquivo_progresso)

    # -- conferencia ---------------------------------------------------------
    def conferir_um(self, it: sqlite3.Row) -> str:
        destino = f"{it['destino_pasta']}/{it['nome_final']}"
        self._mudar(it["item_id"], it["estado"], "EM_VERIFICACAO")
        try:
            info = self.rcd.stat(self.dst_fs, destino)
            if not info:
                self._registrar_verif(it["item_id"], destino, None, None, None, None, "-", "AUSENTE")
                self._mudar(it["item_id"], "EM_VERIFICACAO", "FALHOU", "ausente no destino na conferencia")
                return "AUSENTE"
            n, qx, sha = ler_e_hashear(self.rcd, self.dst_fs, destino)
            if n < (info.get("Size") or 0):
                # stream cortado (ex.: --rc-server-write-timeout de 1 h do rcd): o hash de um trecho nao
                # prova divergencia. Vira ERRO e o item volta a COPIADO para nova conferencia.
                raise IOError(f"leitura incompleta do destino: {n} de {info['Size']} B; repetir a conferencia")
            sha_o = None
            if it["quick_xor_hash"]:
                metodo = "qxh_servidor_x_destino"
                ok = n == it["tamanho"] and _mesmo_qxh(qx, it["quick_xor_hash"])
            else:   # sem hash do servidor: le a origem tambem e compara SHA-256
                metodo = "sha256_origem_x_destino"
                if not self.rcd.stat(self.src_fs, it["caminho_rel"]):
                    self._registrar_verif(it["item_id"], destino, n, qx, sha, None, metodo, "ORIGEM_ALTERADA",
                                          "origem nao encontrada; sem hash do servidor para comparar")
                    self._mudar(it["item_id"], "EM_VERIFICACAO", "ORIGEM_ALTERADA", "origem nao encontrada")
                    return "ORIGEM_ALTERADA"
                n_o, _qx_o, sha_o = ler_e_hashear(self.rcd, self.src_fs, it["caminho_rel"])
                if n_o < it["tamanho"]:
                    raise IOError(f"leitura incompleta da origem: {n_o} de {it['tamanho']} B; repetir a conferencia")
                ok = n == n_o == it["tamanho"] and sha == sha_o
            if not ok:
                det = f"destino {n} B qxh {qx}; esperado {it['tamanho']} B qxh {it['quick_xor_hash']}"
                self._registrar_verif(it["item_id"], destino, n, qx, sha, sha_o, metodo, "DIVERGENTE", det)
                self._mudar(it["item_id"], "EM_VERIFICACAO", "DIVERGENTE", det)
                return "DIVERGENTE"
            # a origem ainda e a mesma do inventario? (so metadado)
            origem = self.rcd.stat(self.src_fs, it["caminho_rel"], hash_=True)
            qx_orig = ((origem or {}).get("Hashes") or {}).get("quickxor")
            if not origem or origem.get("Size") != it["tamanho"] or (
                    qx_orig and it["quick_xor_hash"] and not _mesmo_qxh(qx_orig, it["quick_xor_hash"])):
                self._registrar_verif(it["item_id"], destino, n, qx, sha, sha_o, metodo, "ORIGEM_ALTERADA",
                                      "copia confere com o inventario, mas a origem mudou depois")
                self._mudar(it["item_id"], "EM_VERIFICACAO", "ORIGEM_ALTERADA", "reavaliar a versao")
                return "ORIGEM_ALTERADA"
            self._registrar_verif(it["item_id"], destino, n, qx, sha, sha_o, metodo, "VALIDADO")
            self._mudar(it["item_id"], "EM_VERIFICACAO", "VALIDADO", metodo)
            return "VALIDADO"
        except Exception as e:
            self._registrar_verif(it["item_id"], destino, None, None, None, None, "-", "ERRO", str(e))
            self._mudar(it["item_id"], "EM_VERIFICACAO", "COPIADO", f"conferencia falhou: {e}")
            return "ERRO"

    def conferir(self, lote: str) -> dict:
        itens = self._itens(lote, ("COPIADO", "EM_VERIFICACAO"))
        res: dict[str, int] = {}
        with ThreadPoolExecutor(max_workers=self.concorrencia) as pool:
            for f in as_completed([pool.submit(self.conferir_um, it) for it in itens]):
                r = f.result()
                res[r] = res.get(r, 0) + 1
        return {"lote": lote, "conferidos": len(itens), **res}


def _mesmo_qxh(a: str, b: str) -> bool:
    """O rclone devolve QuickXor em hex; o Graph em base64. Mesmo algoritmo, outra grafia."""
    import base64
    import binascii
    def norm(x: str) -> bytes:
        x = x.strip()
        try:
            if len(x) == 40:
                return binascii.unhexlify(x)
        except binascii.Error:
            pass
        x = x.replace("-", "+").replace("_", "/")
        return base64.b64decode(x + "=" * (-len(x) % 4))      # base64url pode vir sem padding
    try:
        return norm(a) == norm(b)
    except Exception:
        return False

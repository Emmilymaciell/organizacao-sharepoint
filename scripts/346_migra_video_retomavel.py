"""
346_migra_video_retomavel - envio RETOMAVEL de arquivo grande (video de 87,76 GiB) para o PARCEIRO.

Origem: SharePoint lido por intervalos (Graph, Range) - nada vai para o SSD. Destino: Nextcloud
chunking v2 (migra_parceiro/retomavel.py). Queda, reinicio do PC ou erro: rode o MESMO comando de novo;
o servidor informa os blocos ja guardados e so os que faltam sao enviados.

MODOS
  --lote VIDEO-87G       envio real do item do lote (lote aprovado por escrito; fase 'execucao').
                         Ao montar: COPIADO e conferencia integral pelo 337 (rele do PARCEIRO, QuickXor).
  --teste ORIGEM_REL --teste-destino PASTA_TESTE
                         TESTE CONTROLADO (exige autorizacao propria): copia um arquivo ja inventariado
                         para uma pasta de teste, opcionalmente interrompe (--interromper-apos N blocos)
                         e confere o resultado relendo o destino (QuickXor da Microsoft). Nao mexe no banco.

Saida: 0 concluido e VALIDADO (ou teste conferido) | 3 interrompido, retomavel (rodar de novo)
       | 1 bloqueado | 4 divergente | 5 erro estrutural (conflito, 401/403, protocolo): parar e revisar
       | 6 ja ha outra execucao ativa. Pausa: criar tmp/migracao_parceiro/PAUSAR_VIDEO.

Credencial: a mesma senha de aplicativo DPAPI do rcparceiro.ps1, decifrada so em memoria.
Nunca: sobrescreve (Overwrite: F e checagem previa), apaga, renomeia, mexe na origem.

Uso:
  python scripts/346_migra_video_retomavel.py --lote VIDEO-87G [--bloco-mib 64 --paralelo 3]
  python scripts/346_migra_video_retomavel.py --teste "16_Resposta MP 19 06 26/.../Mosaico_RD102_v02.jp2" \\
      --teste-destino "PARCEIRO-INFRA/_TESTE_UPLOAD_RETOMAVEL" --interromper-apos 10
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import os
import re
import subprocess
import sys
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from graph_util import Graph  # noqa: E402
from migra_parceiro.base import agora, cfg, conectar  # noqa: E402
from migra_parceiro.motor import Trava, _mesmo_qxh  # noqa: E402
from migra_parceiro.quickxor import QuickXorHash  # noqa: E402
from migra_parceiro.retomavel import Conflito, Estrutural, FonteGraph, Nextcloud, enviar  # noqa: E402

BASE_DAV = "https://nextcloud.example.com/remote.php/dav"
UID = "usuario"


def senha_dpapi() -> str:
    """Decifra a senha de aplicativo gravada pelo rcparceiro.ps1 (ConvertFrom-SecureString = DPAPI do usuario)."""
    blob = bytes.fromhex((Path(os.environ["APPDATA"]) / "prog-migracao-parceiro" / "parceiro-app.senha.dpapi")
                         .read_text(encoding="ascii").strip())

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    entrada = DATA_BLOB(len(blob), ctypes.cast(ctypes.create_string_buffer(blob, len(blob)), ctypes.POINTER(ctypes.c_char)))
    saida = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(entrada), None, None, None, None, 0, ctypes.byref(saida)):
        raise OSError("DPAPI nao decifrou a senha (usuario diferente?)")
    try:
        return ctypes.string_at(saida.pbData, saida.cbData).decode("utf-16-le")
    finally:
        ctypes.windll.kernel32.LocalFree(saida.pbData)


def manter_acordado(ligar: bool):
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | (0x00000001 if ligar else 0))
    except Exception:
        pass


def conferir_destino(nc: Nextcloud, url: str, qxh_esperado: str, total: int) -> tuple[bool, str, str]:
    """Rele o arquivo inteiro do PARCEIRO e calcula QuickXor + SHA-256 (prova de integridade)."""
    req = urllib.request.Request(url, headers={"Authorization": nc._auth})
    qx, sha, n = QuickXorHash(), hashlib.sha256(), 0
    with urllib.request.urlopen(req, timeout=600) as r:
        while True:
            b = r.read(8 * 2**20)
            if not b:
                break
            qx.update(b)
            sha.update(b)
            n += len(b)
    return (n == total and _mesmo_qxh(qx.base64(), qxh_esperado)), qx.base64(), sha.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--lote")
    ap.add_argument("--teste")
    ap.add_argument("--teste-destino")
    ap.add_argument("--interromper-apos", type=int, default=0, help="(teste) para apos N blocos, para provar a retomada")
    ap.add_argument("--bloco-mib", type=int, default=64)
    ap.add_argument("--paralelo", type=int, default=3)
    a = ap.parse_args()
    c = cfg()
    con = conectar()
    log_arq = RAIZ / "logs" / f"video_retomavel_{time.strftime('%Y%m%d')}.log"

    def log(t):
        linha = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {t}"
        print(linha, flush=True)
        with open(log_arq, "a", encoding="utf-8") as f:
            f.write(linha + "\n")

    if a.lote:
        lt = con.execute("SELECT * FROM lote WHERE nome=?", (a.lote,)).fetchone()
        if not lt or not lt["aprovado_em"] or c.get("fase") != "execucao":
            print(f"BLOQUEADO: lote {a.lote} sem aprovacao escrita ou fase != execucao")
            return 1
        itens = con.execute("SELECT p.item_id, p.estado, p.destino_pasta, p.nome_final, o.drive_id, o.tamanho, "
                            "o.quick_xor_hash, o.caminho_rel FROM plano_item p JOIN origem_item o USING(item_id) "
                            "WHERE p.lote=?", (a.lote,)).fetchall()
        if len(itens) != 1:
            print(f"BLOQUEADO: o lote {a.lote} deve ter exatamente 1 arquivo (tem {len(itens)})")
            return 1
        it = itens[0]
        if it["estado"] == "VALIDADO":
            print("Nada a fazer: estado VALIDADO")
            return 0
        if it["estado"] in ("COPIADO", "EM_VERIFICACAO"):     # montado; falta (ou caiu) a conferencia
            return conferir_lote(a.lote, log)
        destino_rel = f"PARCEIRO-INFRA/{it['destino_pasta']}/{it['nome_final']}"
    elif a.teste and a.teste_destino:
        it = con.execute("SELECT o.item_id, o.drive_id, o.tamanho, o.quick_xor_hash, o.caminho_rel, o.nome AS nome_final "
                         "FROM origem_item o WHERE o.caminho_rel=? AND o.eh_pasta=0", (a.teste,)).fetchone()
        if not it:
            print("ERRO: arquivo de teste nao encontrado no inventario")
            return 1
        destino_rel = f"{a.teste_destino.rstrip('/')}/{it['nome_final']}"
    else:
        ap.print_help()
        return 2

    envio = "prog-migracao-" + re.sub(r"[^A-Za-z0-9]", "", it["item_id"])[-40:] + ("-teste" if a.teste else "")
    nc = Nextcloud(BASE_DAV, UID, c["destino"]["login"], senha_dpapi())
    fonte = FonteGraph(Graph(), it["drive_id"], it["item_id"], it["tamanho"])
    log(f"{'TESTE' if a.teste else 'ENVIO'}: {it['caminho_rel']} ({it['tamanho'] / 2**30:.2f} GiB) -> {destino_rel}")
    contador = {"n": 0}
    pausa = RAIZ / "tmp" / "migracao_parceiro" / "PAUSAR_VIDEO"
    parar = (lambda: bool(a.interromper_apos and contador["n"] >= a.interromper_apos) or pausa.exists())
    enviar_bloco_orig = nc.enviar_bloco

    def contar(*x, **k):
        enviar_bloco_orig(*x, **k)
        contador["n"] += 1
    nc.enviar_bloco = contar

    manter_acordado(True)
    try:
        with Trava(con, nome="video"):
            r = enviar(fonte, nc, destino_rel, envio, a.bloco_mib * 2**20, paralelo=a.paralelo,
                       checkpoint=RAIZ / "tmp" / "migracao_parceiro" / f"{envio}.json", parar=parar, log=log)
            log(f"resultado: {r}; blocos enviados nesta execucao: {contador['n']}")
            if r["situacao"] != "MONTADO":
                log("interrompido; rode o mesmo comando para retomar")
                return 3
            if a.teste:
                ok, qx, sha = conferir_destino(nc, nc.url_arquivo(destino_rel), it["quick_xor_hash"], it["tamanho"])
                log(f"TESTE conferencia: {'OK - identico a origem' if ok else 'DIVERGENTE'} qxh={qx} sha256={sha}")
                return 0 if ok else 4
            q = agora()
            con.execute("UPDATE plano_item SET estado='COPIADO', atualizado_em=? WHERE item_id=?", (q, it["item_id"]))
            con.execute("INSERT INTO evento (item_id, quando, de_estado, para_estado, detalhe) VALUES (?,?,?,?,?)",
                        (it["item_id"], q, it["estado"], "COPIADO", f"upload retomavel montado: {r}"))
            con.execute("INSERT INTO transferencia (item_id, lote, tentativa, inicio, fim, bytes, segundos, resultado) "
                        "VALUES (?,?,?,?,?,?,?,?)", (it["item_id"], a.lote, 1, q, q, it["tamanho"], None, "COPIADO"))
            con.commit()
    except RuntimeError as e:
        if "execucao ativa" not in str(e):
            raise
        log(f"outra execucao do video ja esta ativa: {e}")
        return 6
    except (Conflito, Estrutural) as e:
        log(f"PARADA ESTRUTURAL ({type(e).__name__}): {e} - blocos e checkpoint preservados; revisar antes de rodar de novo")
        return 5
    except Exception as e:
        log(f"falha transitoria: {type(e).__name__}: {e} - rode o mesmo comando para retomar")
        return 3
    finally:
        manter_acordado(False)
    return conferir_lote(a.lote, log)


def conferir_lote(lote: str, log) -> int:
    log("conferencia integral pelo 337 (rele o arquivo inteiro do PARCEIRO)")
    rc = subprocess.run([sys.executable, "-X", "utf8", str(RAIZ / "scripts" / "337_migra_conferir.py"), lote,
                         "--concorrencia", "1"], cwd=RAIZ).returncode
    con = conectar()
    est = con.execute("SELECT estado FROM plano_item WHERE lote=?", (lote,)).fetchone()[0]
    log(f"conferencia terminou: 337 saiu {rc}; estado {est}")
    if est == "VALIDADO":
        return 0
    return 3 if est in ("COPIADO", "EM_VERIFICACAO") else 4


if __name__ == "__main__":
    raise SystemExit(main())

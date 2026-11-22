"""
340_migra_prerequisitos - confere TUDO antes de escrever no PARCEIRO. Somente leitura.

Os 10 requisitos da autorizacao de 19/10/2026 (execucao controlada):
  1 manifesto sem ambiguidade        2 sem sobreposicao entre lotes
  3 conciliado com o inventario      4 caminhos finais seguem a EAP
  5 pastas existentes reutilizadas   6 sem colisao/sobrescrita/operacao destrutiva
  7 conexao SharePoint -> PARCEIRO       8 controles (disco, concorrencia, integridade, retomada)
  9 origem igual ao inventario (amostra) + checagem por arquivo no motor
 10 recuperacao testada (testes de retomada/pausa/queda de rede/401, rodados agora)
Sai com 0 so se todos passarem. O 339 nao executa um lote cujo 340 falhou.

Uso:
    python scripts/340_migra_prerequisitos.py PROD-01-PRODUTOS [--esperado N] [--sem-testes]
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import unicodedata
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_parceiro.base import cfg, conectar  # noqa: E402
from migra_parceiro.espaco import GIB, Guarda  # noqa: E402
from migra_parceiro.motor import _mesmo_qxh  # noqa: E402
from migra_parceiro.nomes import chave  # noqa: E402
from migra_parceiro.prerequisitos import checar_lote  # noqa: E402
from migra_parceiro.rc import METODOS_PERMITIDOS  # noqa: E402

WRAPPER = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(RAIZ / "scripts" / "rcparceiro.ps1")]


def rclone(*args) -> tuple[int, list[str]]:
    p = subprocess.run(WRAPPER + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, [l for l in p.stdout.splitlines() if l.strip()]


def k(s: str) -> str:
    return unicodedata.normalize("NFC", s).casefold()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("lote")
    ap.add_argument("--esperado", type=int)
    ap.add_argument("--sem-testes", action="store_true", help="nao roda os testes de recuperacao")
    a = ap.parse_args()
    c = cfg()
    con = conectar()
    t = c["transferencia"]
    tam_max = int(t.get("tamanho_max_gib_por_arquivo") or 0) * GIB or None
    res: list[tuple[str, bool, str]] = []

    # 7. conexao nas duas pontas
    dst, src = c["remotes"]["destino"], c["remotes"]["origem"]
    cod, linhas = rclone("lsd", dst)
    res.append(("7. PARCEIRO autenticado (WebDAV)", cod == 0 and len(linhas) > 0, f"{dst}: {len(linhas)} pastas"))
    cod_s, linhas_s = rclone("lsd", src)
    res.append(("7. SharePoint autenticado (Graph/rclone)", cod_s == 0 and len(linhas_s) > 0,
                f"{src}: {len(linhas_s)} pastas"))

    res += checar_lote(con, a.lote, a.esperado, tam_max)

    # 5/6. destino ao vivo: o que falta criar, o que ja existe, nada a sobrescrever
    itens = con.execute("SELECT p.estado, p.destino_pasta, p.nome_final, o.caminho_rel, o.tamanho, "
                        "o.quick_xor_hash FROM plano_item p JOIN origem_item o USING(item_id) WHERE p.lote=?",
                        (a.lote,)).fetchall()
    na_fila = [i for i in itens if i["estado"] in ("AGUARDANDO_APROVACAO", "APROVADO", "FALHOU")]
    raizes = sorted({i["destino_pasta"].split("/")[0] for i in itens})
    vivo, ok_lista = set(), True
    for r in raizes:
        cod, ls = rclone("lsf", "-R", f"{dst}/{r}")
        ok_lista &= cod == 0
        vivo.add(k(r))
        vivo |= {k(f"{r}/{x.rstrip('/')}") for x in ls}
    ja = [f"{i['destino_pasta']}/{i['nome_final']}" for i in na_fila
          if k(f"{i['destino_pasta']}/{i['nome_final']}") in vivo]
    res.append(("6. nenhum arquivo ainda na fila ja existe no destino (sem sobrescrita)", ok_lista and not ja,
                f"{len(ja)} ja existem" + (f": {ja[:2]}" if ja else "") + f" (fila: {len(na_fila)})"))
    criar, reusar = set(), set()
    pais_reais: dict[str, set[str]] = {}
    for x in vivo:
        pai, _, nome = x.rpartition("/")
        pais_reais.setdefault(pai, set()).add(chave(nome))
    equivalente_paralelo = []
    for i in itens:
        segs = i["destino_pasta"].split("/")
        for n in range(1, len(segs) + 1):
            q = "/".join(segs[:n])
            if k(q) in vivo:
                reusar.add(q)
            else:
                criar.add(q)
                pai = "/".join(segs[:n - 1])
                irmaos = pais_reais.get(k(pai), set())
                if chave(segs[n - 1]) in irmaos:   # existe com outra grafia: criar seria estrutura paralela
                    equivalente_paralelo.append(q)
    res.append(("5. pastas existentes reutilizadas; nenhuma paralela a uma equivalente",
                not equivalente_paralelo,
                f"reusar {len(reusar)}, criar {len(criar)}"
                + (f"; PARALELAS: {equivalente_paralelo[:2]}" if equivalente_paralelo else "")))

    # 8. controles
    g = Guarda.do_config(RAIZ, c)
    ok, motivo = g.pode_iniciar(0)
    res.append(("8. disco acima da reserva; temporarios no teto", ok,
                f"livre {g.livre() / GIB:.1f} GiB; reserva {g.reserva_minima / GIB:.0f}; teto tmp {g.teto_temporario / GIB:.0f}"))
    cfg_ok = all(t.get(x) for x in ("concorrencia", "concorrencia_min", "concorrencia_max", "max_tentativas",
                                    "tentativas_por_arquivo", "memoria_max_mb")) and bool(t.get("conferir_em_linha"))
    res.append(("8. concorrencia adaptativa, retentativas, memoria e conferencia em linha configuradas", cfg_ok,
                f"conc {t.get('concorrencia')} [{t.get('concorrencia_min')}-{t.get('concorrencia_max')}], "
                f"mem {t.get('memoria_max_mb')} MB, conferir_em_linha={t.get('conferir_em_linha')}"))
    wr = (RAIZ / "scripts" / "rcparceiro.ps1").read_text(encoding="ascii")
    destrutivos = {"operations/delete", "operations/purge", "operations/movefile", "sync/sync", "sync/move",
                   "operations/deletefile", "operations/rmdir", "operations/rmdirs", "operations/cleanup"}
    res.append(("6. sem operacao destrutiva possivel (wrapper por lista permitida; cliente rc so le/copia)",
                "$permitidos" in wr and not (METODOS_PERMITIDOS & destrutivos),
                ", ".join(sorted(METODOS_PERMITIDOS))))

    # 9. origem x inventario: amostra lida agora no SharePoint (o motor ainda confere CADA arquivo antes de copiar)
    amostra = random.Random(a.lote).sample(list(na_fila), min(25, len(na_fila))) if na_fila else []
    divergentes = []
    if amostra:
        lst = RAIZ / "tmp" / "migracao_parceiro" / f"amostra_{a.lote}.txt"
        lst.parent.mkdir(parents=True, exist_ok=True)
        lst.write_text("\n".join(i["caminho_rel"] for i in amostra), encoding="utf-8")
        # -R e obrigatorio: sem ele o lsjson com --files-from so olha a raiz e devolve vazio
        cod, out = rclone("lsjson", src, "-R", "--files-from", str(lst), "--hash", "--files-only", "--no-mimetype")
        vistos = {}
        if cod == 0:
            for o in json.loads("\n".join(out) or "[]"):
                vistos[o["Path"]] = o
        for i in amostra:
            o = vistos.get(i["caminho_rel"])
            qx = ((o or {}).get("Hashes") or {}).get("quickxor", "")
            if not o or o.get("Size") != i["tamanho"] or (i["quick_xor_hash"] and not _mesmo_qxh(qx, i["quick_xor_hash"])):
                divergentes.append(i["caminho_rel"])
        lst.unlink(missing_ok=True)
    res.append(("9. origem igual ao inventario (amostra lida agora; motor confere cada arquivo)", not divergentes,
                f"amostra {len(amostra)}: {len(divergentes)} divergentes" + (f": {divergentes[:2]}" if divergentes else "")
                + " | divergencia futura -> ORIGEM_ALTERADA, sem copia"))

    # 10. recuperacao: os testes de retomada/pausa/queda/401 rodam agora
    if not a.sem_testes:
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(RAIZ / "tests" / "test_migra_motor.py"),
                            "-k", "retomada or pausa or queda_de_rede or autenticacao or trava or transitorio"],
                           capture_output=True, text=True, cwd=RAIZ)
        ultima = (p.stdout.strip().splitlines() or ["?"])[-1]
        res.append(("10. recuperacao testada agora (retomada, pausa, queda de rede, 401, trava)", p.returncode == 0, ultima))

    falhou = False
    for nome, ok, det in res:
        print(f"[{'OK ' if ok else 'FALHOU'}] {nome}: {det}")
        falhou |= not ok
    print(f"\nPRE-REQUISITOS {a.lote}: " + ("NAO ATENDIDOS - o lote NAO sera executado" if falhou else "TODOS ATENDIDOS"))
    return 1 if falhou else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
390_ged_zip_ctd - empacotamento das pastas CTD da RD-103 (DESAPROPRIACAO, REV 03) em ZIPs < 100 MB
para entrega no GED. Tarefa pontual, pedida pela Emmily em 11/11/2026 (S09).

Fases:
  python scripts/390_ged_zip_ctd.py inventario   # somente leitura da origem
  python scripts/390_ged_zip_ctd.py executar     # gera os ZIPs (local, um por vez) e copia para a saida
  python scripts/390_ged_zip_ctd.py validar      # reabre cada ZIP NA SAIDA e confere CRC + SHA-256 + tamanho
  python scripts/390_ged_zip_ctd.py relatorio    # CSV + XLSX de controle (local e na saida)

Garantias:
  * A origem so e aberta para leitura ('rb'). Nada e criado, movido ou apagado nela.
  * A unica escrita no I: e dentro da pasta de saida nova (_PACOTES_ZIP_GED_REV03[_data]).
    Arquivo existente na saida nunca e sobrescrito (o .part e nosso; o .zip final, so se for nosso e invalido).
  * Cada ZIP e montado no scratch local (<= ~90 MB por vez), testado, e so entao copiado como .part e
    renomeado. Limite: zip atual + tamanho original do proximo arquivo * 1.002 + 64 KiB <= 90.000.000 B,
    entao nenhum ZIP pode passar de 90 MB (deflate nunca expande mais que ~0,03%).
  * Arquivo isolado > limite: tentado sozinho; se o ZIP passar de 99.000.000 B, vira EXCECAO (nao omitido).
  * Prioridade de processo BELOW_NORMAL; leitura em blocos de 1 MiB; sequencial.

Retomada / idempotencia (revisao S09):
  * 'executar' pula pastas ja marcadas 'completo' no estado.json; se cair no meio de uma pasta com varias partes,
    a nova rodada ABORTA ao encontrar o .zip ja copiado (nao sobrescreve) - conferir e decidir manualmente.
  * 'validar' so le (grava apenas o estado.json local).
  * 'relatorio' REGRAVA os 2 arquivos de controle na saida do I: (so os seus): e escrita no PARCEIRO.
  * Executado em 11/11/2026 (D-16). Depois, a Emmily moveu os ZIPs para dentro da REV 03 e apagou a saida e as
    pastas de origem: NAO rodar nenhuma fase de novo (a origem nao existe mais).
"""

from __future__ import annotations

import csv
import ctypes
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

RAIZ_PROJ = Path(__file__).resolve().parent.parent
DESAP = Path(r"I:\05. RD-103\04. Engenharia\01. Projetos\02. Executivo\03. VIARIO SEC\DESAPROPRIAÇÃO")
ORIGEM = DESAP / "REV 03 - DIVISÃO DAS PASTAS GED"
SAIDA_BASE = "_PACOTES_ZIP_GED_REV03"
LOCAL = RAIZ_PROJ / "output" / "ged_rd103_rev03"
ESTADO = LOCAL / "estado.json"
INVENT = LOCAL / "inventario.json"

LIMITE_OP = 90_000_000        # alvo operacional
LIMITE_DURO = 100_000_000     # regra do GED (estritamente menor)
LIMITE_EXC = 99_000_000       # arquivo isolado: aceito se o zip ficar abaixo disto
BLOCO = 1 << 20
RE_CTD = re.compile(r"^\d+\.\s*CTD\s*\d+", re.I)


def baixa_prioridade():
    try:
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)
    except Exception:
        pass


def agora():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def jload(p: Path, padrao):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else padrao


def jsave(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


# ---------------------------------------------------------------- inventario (somente leitura)
def inventario():
    if not ORIGEM.is_dir():
        sys.exit(f"origem inexistente: {ORIGEM}")
    pastas, soltos = [], []
    for e in sorted(os.scandir(ORIGEM), key=lambda e: e.name):
        if e.is_dir():
            pastas.append(e.name)
        else:
            st = e.stat()
            soltos.append({"nome": e.name, "bytes": st.st_size})
    unidades = []
    for nome in pastas:
        base = ORIGEM / nome
        arquivos, subpastas, erros = [], 0, []
        for dirpath, dirnames, filenames in os.walk(base, onerror=lambda ex: erros.append(str(ex))):
            dirnames.sort()
            subpastas += len(dirnames)
            for f in sorted(filenames):
                p = Path(dirpath) / f
                try:
                    st = p.stat()
                    arquivos.append({"rel": p.relative_to(base).as_posix(), "bytes": st.st_size,
                                     "mtime": st.st_mtime_ns})
                except OSError as ex:
                    erros.append(f"{p}: {ex}")
        total = sum(a["bytes"] for a in arquivos)
        grandes = [a for a in arquivos if a["bytes"] > LIMITE_OP]
        unidades.append({"pasta": nome, "padrao_ctd": bool(RE_CTD.match(nome)), "arquivos": arquivos,
                         "n_arquivos": len(arquivos), "n_subpastas": subpastas, "bytes": total,
                         "grandes": [g["rel"] for g in grandes], "erros": erros,
                         "zips_estimados": max(1, -(-total // LIMITE_OP)) if arquivos else 0})
        print(f"{nome:40s} {len(arquivos):6d} arq {subpastas:4d} sub {total/1e6:10.1f} MB"
              f"  grandes>{LIMITE_OP/1e6:.0f}MB: {len(grandes)}  erros: {len(erros)}"
              + ("" if RE_CTD.match(nome) else "  [FORA DO PADRAO CTD]"))
    inv = {"origem": str(ORIGEM), "lido_em": agora(), "unidades": unidades, "soltos_na_raiz": soltos}
    jsave(INVENT, inv)
    tot = sum(u["bytes"] for u in unidades)
    print(f"\npastas: {len(unidades)} | arquivos: {sum(u['n_arquivos'] for u in unidades)} | "
          f"{tot/1e6:.1f} MB ({tot/2**30:.3f} GiB) | soltos na raiz: {len(soltos)} {soltos}")
    print(f"C: livre: {shutil.disk_usage('C:/').free/2**30:.1f} GiB | inventario: {INVENT}")


# ---------------------------------------------------------------- execucao
def pasta_saida(estado: dict) -> Path:
    if estado.get("saida"):
        return Path(estado["saida"])
    alvo = DESAP / SAIDA_BASE
    if alvo.exists():
        alvo = DESAP / f"{SAIDA_BASE}_{dt.date.today():%Y%m%d}"
        if alvo.exists():
            sys.exit(f"saidas ja existem ({alvo}); nada foi feito. Verificar manualmente.")
    return alvo


def nome_zip(pasta: str, parte: int, total: int | None) -> str:
    return f"{pasta}.zip" if total == 1 else f"{pasta} - PARTE {parte:02d}.zip"


def executar():
    baixa_prioridade()
    inv = jload(INVENT, None) or sys.exit("rode 'inventario' antes")
    estado = jload(ESTADO, {"pacotes": {}, "excecoes": []})
    saida = pasta_saida(estado)
    if not estado.get("saida"):
        saida.mkdir()                      # unica pasta criada no I:
        estado["saida"] = str(saida)
        estado["criada_em"] = agora()
        jsave(ESTADO, estado)
    print(f"saida: {saida}")
    scratch = Path(tempfile.gettempdir()) / "prog_ged_zip"
    scratch.mkdir(exist_ok=True)

    for u in inv["unidades"]:
        pasta = u["pasta"]
        if not u["arquivos"]:
            estado["excecoes"].append({"pasta": pasta, "rel": "", "motivo": "pasta sem arquivos"})
            continue
        if estado["pacotes"].get(pasta, {}).get("completo"):
            continue
        base = ORIGEM / pasta
        partes = []                         # cada parte: {"zip_local", "membros": [...]}
        fila = list(u["arquivos"])
        i_parte = 0
        excecoes = []
        while fila:
            i_parte += 1
            local = scratch / f"{pasta} - p{i_parte:02d}.zip"
            if local.exists():
                local.unlink()
            membros = []
            with zipfile.ZipFile(local, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as zf:
                while fila:
                    a = fila[0]
                    atual = zf.fp.tell()
                    previsto = atual + int(a["bytes"] * 1.002) + 65536 + 2 * len(a["rel"].encode())
                    if membros and previsto > LIMITE_OP:
                        break
                    fila.pop(0)
                    src = base / Path(a["rel"])
                    st = src.stat()
                    zi = zipfile.ZipInfo.from_file(src, arcname=f"{pasta}/{a['rel']}")
                    zi.compress_type = zipfile.ZIP_DEFLATED
                    h = hashlib.sha256()
                    lidos = 0
                    with open(src, "rb") as fi, zf.open(zi, "w", force_zip64=st.st_size > 2**31) as fo:
                        while b := fi.read(BLOCO):
                            h.update(b)
                            fo.write(b)
                            lidos += len(b)
                    membros.append({"rel": a["rel"], "bytes": lidos, "sha256": h.hexdigest(),
                                    "mtime_ns": st.st_mtime_ns, "inv_bytes": a["bytes"]})
                    if not membros[:-1] and a["bytes"] > LIMITE_OP:
                        break               # arquivo grande vai sozinho
            tam = local.stat().st_size
            if tam >= LIMITE_EXC:
                excecoes.append({"pasta": pasta, "rel": membros[0]["rel"] if len(membros) == 1 else "?",
                                 "motivo": f"zip isolado com {tam} B >= {LIMITE_EXC} B; nao entregue",
                                 "bytes": membros[0]["bytes"] if len(membros) == 1 else None})
                local.unlink()
                i_parte -= 1
                if len(membros) != 1:
                    sys.exit(f"parte inesperadamente grande em {pasta}; abortado")
                continue
            partes.append({"local": str(local), "membros": membros, "zip_bytes": tam})
            print(f"  {pasta} parte {i_parte}: {len(membros)} arq, {tam/1e6:.2f} MB")

        # testa localmente e copia
        n = len(partes)
        reg = {"pasta": pasta, "partes": [], "completo": False}
        for k, p in enumerate(partes, 1):
            nome = nome_zip(pasta, k, n)
            local = Path(p["local"])
            with zipfile.ZipFile(local) as zf:
                ruim = zf.testzip()
            if ruim:
                sys.exit(f"CRC local falhou em {nome}: {ruim}")
            sha_zip = sha_arquivo(local)
            destino = saida / nome
            if destino.exists():
                sys.exit(f"ja existe na saida, nao sobrescrevo: {destino}")
            parcial = saida / (nome + ".part")
            if parcial.exists():
                parcial.unlink()            # .part e sempre nosso (criado so por este script)
            shutil.copyfile(local, parcial)
            os.replace(parcial, destino)
            local.unlink()
            reg["partes"].append({"zip": nome, "parte": k, "de": n, "zip_bytes": p["zip_bytes"],
                                  "zip_sha256": sha_zip, "membros": p["membros"], "copiado_em": agora()})
            print(f"  -> {nome} ({p['zip_bytes']/1e6:.2f} MB) copiado")
        reg["completo"] = True
        estado["pacotes"][pasta] = reg
        estado["excecoes"] = [e for e in estado["excecoes"] if e["pasta"] != pasta] + excecoes
        jsave(ESTADO, estado)
    estado["executado_em"] = agora()
    jsave(ESTADO, estado)
    print("execucao concluida")


def sha_arquivo(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while b := f.read(BLOCO):
            h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------- validacao (na saida)
def validar():
    baixa_prioridade()
    inv = jload(INVENT, None)
    estado = jload(ESTADO, None) or sys.exit("nada executado")
    saida = Path(estado["saida"])
    problemas = []
    vistos = {}
    for u in inv["unidades"]:
        pasta = u["pasta"]
        reg = estado["pacotes"].get(pasta)
        exc = {e["rel"] for e in estado["excecoes"] if e["pasta"] == pasta}
        esperados = {a["rel"]: a for a in u["arquivos"]}
        cobertos = {}
        for p in (reg or {}).get("partes", []):
            z = saida / p["zip"]
            res = {"existe": z.exists()}
            if not z.exists():
                p["validacao"] = {"ok": False, "erro": "zip ausente na saida"}
                problemas.append(p["zip"])
                continue
            tam = z.stat().st_size
            erros = []
            if tam != p["zip_bytes"]:
                erros.append(f"tamanho {tam} != {p['zip_bytes']}")
            if tam >= LIMITE_DURO:
                erros.append("tamanho >= 100.000.000 B")
            if sha_arquivo(z) != p["zip_sha256"]:
                erros.append("sha256 do zip difere do gerado")
            esperado_m = {f"{pasta}/{m['rel']}": m for m in p["membros"]}
            with zipfile.ZipFile(z) as zf:
                nomes = zf.namelist()
                if len(nomes) != len(set(nomes)):
                    erros.append("nome repetido dentro do zip")
                if set(nomes) != set(esperado_m):
                    erros.append("lista de membros difere do registro")
                for nome in nomes:
                    m = esperado_m.get(nome)
                    if not nome.startswith(pasta + "/"):
                        erros.append(f"membro fora da pasta CTD: {nome}")
                    h = hashlib.sha256()
                    n = 0
                    with zf.open(nome) as f:   # CRC conferido pelo zipfile ao fim da leitura
                        while b := f.read(BLOCO):
                            h.update(b)
                            n += len(b)
                    if m and (h.hexdigest() != m["sha256"] or n != m["bytes"]):
                        erros.append(f"conteudo difere: {nome}")
                    rel = nome[len(pasta) + 1:]
                    if rel in cobertos or rel in vistos.get(pasta, set()):
                        erros.append(f"duplicado entre partes: {rel}")
                    cobertos[rel] = p["zip"]
            p["validacao"] = {"ok": not erros, "erros": erros, "em": agora(), "zip_bytes_saida": tam}
            if erros:
                problemas.append(p["zip"])
            print(f"{'OK ' if not erros else 'FALHA'} {p['zip']} {tam/1e6:.2f} MB {len(nomes)} arq {erros[:3]}")
        faltando = [r for r in esperados if r not in cobertos and r not in exc]
        # originais intactos: tamanho e mtime iguais ao inventario e ao lido na compactacao
        alterados = []
        for r, a in esperados.items():
            try:
                st = (ORIGEM / pasta / Path(r)).stat()
                if st.st_size != a["bytes"] or st.st_mtime_ns != a["mtime"]:
                    alterados.append(r)
            except OSError:
                alterados.append(r)
        for p in (reg or {}).get("partes", []):
            for m in p["membros"]:
                if m["bytes"] != m["inv_bytes"]:
                    alterados.append(m["rel"])
        extras = sorted(_novos(ORIGEM / pasta, esperados))
        if reg:
            reg["cobertura"] = {"esperados": len(esperados), "cobertos": len(cobertos), "excecoes": len(exc),
                                "faltando": faltando, "originais_alterados": sorted(set(alterados)),
                                "novos_na_origem": extras}
        if faltando or alterados:
            problemas.append(f"{pasta}: faltando={len(faltando)} alterados={len(set(alterados))}")
        print(f"   {pasta}: {len(cobertos)}/{len(esperados)} cobertos, excecoes {len(exc)}, "
              f"faltando {len(faltando)}, originais alterados {len(set(alterados))}, novos {len(extras)}")
    # pasta de saida: so nossos zips
    nossos = {p["zip"] for r in estado["pacotes"].values() for p in r["partes"]}
    estranhos = sorted(set(os.listdir(saida)) - nossos - {"CONTROLE_GED_RD103_REV03.xlsx",
                                                           "CONTROLE_GED_RD103_REV03.csv"})
    estado["validado_em"] = agora()
    estado["problemas"] = problemas
    estado["estranhos_na_saida"] = estranhos
    # raiz da origem
    estado["origem_raiz_agora"] = sorted(os.listdir(ORIGEM))
    jsave(ESTADO, estado)
    print(f"\nproblemas: {problemas or 'nenhum'} | itens estranhos na saida: {estranhos or 'nenhum'}")


def _novos(base: Path, esperados: dict) -> list[str]:
    novos = []
    for dirpath, _, files in os.walk(base):
        for f in files:
            r = (Path(dirpath) / f).relative_to(base).as_posix()
            if r not in esperados:
                novos.append(r)
    return novos


# ---------------------------------------------------------------- relatorio
COLS = ["CTD", "Pasta original", "Tamanho original (MB)", "Qtde arquivos originais", "Nome do ZIP",
        "Parte", "Total de partes", "Tamanho do ZIP (MB)", "Tamanho do ZIP (bytes)", "Documentos no ZIP",
        "Integridade verificada", "Status", "Observacoes", "SHA-256 do ZIP"]


def relatorio():
    inv = jload(INVENT, None)
    estado = jload(ESTADO, None)
    saida = Path(estado["saida"])
    linhas = []
    for u in inv["unidades"]:
        reg = estado["pacotes"].get(u["pasta"], {})
        exc = [e for e in estado["excecoes"] if e["pasta"] == u["pasta"]]
        cob = reg.get("cobertura", {})
        for p in reg.get("partes", []):
            v = p.get("validacao", {})
            ok = v.get("ok") and not cob.get("faltando") and not cob.get("originais_alterados")
            obs = []
            if p["de"] > 1:
                obs.append(f"enviar as {p['de']} partes desta CTD")
            if v.get("erros"):
                obs.append("; ".join(v["erros"][:3]))
            if cob.get("faltando"):
                obs.append(f"{len(cob['faltando'])} arquivo(s) sem pacote")
            if cob.get("novos_na_origem"):
                obs.append(f"{len(cob['novos_na_origem'])} arquivo(s) novo(s) na origem apos o inventario")
            if exc:
                obs.append(f"{len(exc)} excecao(oes) na CTD")
            primeiro, ultimo = p["membros"][0]["rel"], p["membros"][-1]["rel"]
            if p["de"] > 1:
                obs.append(f"de '{primeiro}' a '{ultimo}'")
            linhas.append([u["pasta"].split(".", 1)[-1].strip() if u["padrao_ctd"] else u["pasta"],
                           str(ORIGEM / u["pasta"]), round(u["bytes"] / 1e6, 2), u["n_arquivos"], p["zip"],
                           p["parte"], p["de"], round(p["zip_bytes"] / 1e6, 2), p["zip_bytes"], len(p["membros"]),
                           "SIM" if v.get("ok") else "NAO", "APROVADO PARA ENTREGA" if ok else "PENDENTE",
                           " | ".join(obs), p["zip_sha256"]])
        for e in exc:
            linhas.append([u["pasta"], str(ORIGEM / u["pasta"]), round(u["bytes"] / 1e6, 2), u["n_arquivos"],
                           "", "", "", "", "", "", "NAO", "EXCECAO", f"{e['rel']}: {e['motivo']}", ""])
    total_arq = sum(u["n_arquivos"] for u in inv["unidades"])
    vol = sum(u["bytes"] for u in inv["unidades"])
    zips = [l for l in linhas if l[4]]
    vz = sum(l[8] for l in zips)
    aprov = sum(1 for l in zips if l[11] == "APROVADO PARA ENTREGA")
    resumo = [("Total de pastas (CTDs)", len(inv["unidades"])),
              ("Pastas no padrao CTD", sum(u["padrao_ctd"] for u in inv["unidades"])),
              ("Total de arquivos originais", total_arq),
              ("Volume original (MB)", round(vol / 1e6, 2)), ("Volume original (GiB)", round(vol / 2**30, 3)),
              ("ZIPs gerados", len(zips)), ("Volume compactado (MB)", round(vz / 1e6, 2)),
              ("Reducao (%)", round(100 * (1 - vz / vol), 2) if vol else 0),
              ("Maior ZIP (bytes)", max((l[8] for l in zips), default=0)),
              ("Pacotes aprovados para entrega", aprov), ("Pacotes pendentes/problematicos", len(zips) - aprov),
              ("Excecoes", len(estado["excecoes"])), ("Arquivos soltos na raiz da REV 03", len(inv["soltos_na_raiz"])),
              ("Origem", str(ORIGEM)), ("Pasta dos ZIPs", str(saida)),
              ("Inventario lido em", inv["lido_em"]), ("Validado em", estado.get("validado_em", "")),
              ("Gerado por", "scripts/390_ged_zip_ctd.py (projeto PROG_Organizacao_SharePoint)")]
    LOCAL.mkdir(parents=True, exist_ok=True)
    csv_local = LOCAL / "CONTROLE_GED_RD103_REV03.csv"
    with open(csv_local, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(COLS)
        w.writerows(linhas)
    # lista arquivo a arquivo (rastreabilidade)
    with open(LOCAL / "ARQUIVOS_POR_ZIP.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["CTD", "ZIP", "Caminho relativo", "Bytes", "SHA-256"])
        for pasta, reg in estado["pacotes"].items():
            for p in reg["partes"]:
                for m in p["membros"]:
                    w.writerow([pasta, p["zip"], m["rel"], m["bytes"], m["sha256"]])

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    ws = wb.active
    ws.title = "Pacotes"
    ws.append(COLS)
    for l in linhas:
        ws.append(l)
    cab = PatternFill("solid", fgColor="1F4E78")
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = cab
        c.alignment = Alignment(wrap_text=True, vertical="center")
    verde, amarelo = PatternFill("solid", fgColor="C6EFCE"), PatternFill("solid", fgColor="FFEB9C")
    for row in ws.iter_rows(min_row=2):
        row[11].fill = verde if row[11].value == "APROVADO PARA ENTREGA" else amarelo
    larg = [12, 60, 12, 10, 34, 7, 8, 11, 13, 10, 11, 22, 60, 66]
    for i, w_ in enumerate(larg, 1):
        ws.column_dimensions[get_column_letter(i)].width = w_
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    r = wb.create_sheet("Resumo")
    for k, v in resumo:
        r.append([k, v])
    r.column_dimensions["A"].width = 38
    r.column_dimensions["B"].width = 110
    for c in r["A"]:
        c.font = Font(bold=True)
    a = wb.create_sheet("Arquivos")
    a.append(["CTD", "ZIP", "Caminho relativo", "Bytes", "SHA-256"])
    for pasta, reg in estado["pacotes"].items():
        for p in reg["partes"]:
            for m in p["membros"]:
                a.append([pasta, p["zip"], m["rel"], m["bytes"], m["sha256"]])
    for col, w_ in zip("ABCDE", [16, 34, 90, 13, 66]):
        a.column_dimensions[col].width = w_
    if estado["excecoes"] or inv["soltos_na_raiz"]:
        e = wb.create_sheet("Excecoes")
        e.append(["Pasta", "Arquivo", "Motivo"])
        for x in estado["excecoes"]:
            e.append([x["pasta"], x["rel"], x["motivo"]])
        for s in inv["soltos_na_raiz"]:
            e.append(["(raiz da REV 03)", s["nome"], "arquivo solto fora das CTDs"])
    xlsx_local = LOCAL / "CONTROLE_GED_RD103_REV03.xlsx"
    wb.save(xlsx_local)
    for arq in (csv_local, xlsx_local):
        dest = saida / arq.name
        if dest.exists() and arq.name not in estado.get("relatorios_nossos", []):
            sys.exit(f"ja existe e nao e nosso: {dest}")
        shutil.copyfile(arq, dest)
    estado["relatorios_nossos"] = [csv_local.name, xlsx_local.name]
    jsave(ESTADO, estado)
    for k, v in resumo:
        print(f"{k:40s} {v}")


if __name__ == "__main__":
    {"inventario": inventario, "executar": executar, "validar": validar, "relatorio": relatorio}[sys.argv[1]]()

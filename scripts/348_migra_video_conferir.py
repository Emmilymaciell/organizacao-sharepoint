"""
348_migra_video_conferir - conferencia SOMENTE LEITURA do video do lote VIDEO-87G, direto no PARCEIRO (WebDAV).

Por que existe: o 337 le pelo `rclone rcd --rc-serve`, cujo servidor HTTP corta qualquer resposta com
1 h (--rc-server-write-timeout, padrao 1h0m0s). O video leva mais que isso; o corte chega como fim de
stream, e o hash de um arquivo TRUNCADO foi comparado como se fosse o arquivo inteiro (DIVERGENTE falso).

Aqui a leitura vai direto ao Nextcloud, por intervalos (Range), retomando do ultimo byte se a conexao
cair, e so ha veredito se os bytes lidos == tamanho esperado.

  --origem     metadados atuais da origem (Graph) x inventario e tamanho atual do destino
  --regioes    compara SHA-256 de trechos de 16 MiB origem x destino (inicio, fim, blocos indicados,
               ponto de corte da leitura anterior)
  --integral   rele o destino inteiro: QuickXor (x Microsoft) e SHA-256; progresso em JSON

Nao altera o banco, a origem nem o destino. Resultado em output/migracao_parceiro/video_conferencia_*.json.

Uso:
  python scripts/348_migra_video_conferir.py --origem --regioes --blocos 1669 1670
  python scripts/348_migra_video_conferir.py --integral
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import time
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from graph_util import Graph  # noqa: E402
from migra_parceiro.base import cfg, conectar  # noqa: E402
from migra_parceiro.motor import _mesmo_qxh  # noqa: E402
from migra_parceiro.quickxor import QuickXorHash  # noqa: E402
from migra_parceiro.retomavel import FonteGraph, Nextcloud  # noqa: E402

MiB = 2**20
BLOCO_ENVIO = 16 * MiB
SAIDA = RAIZ / "output" / "migracao_parceiro"

_spec = importlib.util.spec_from_file_location("m346", RAIZ / "scripts" / "346_migra_video_retomavel.py")
_m346 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m346)


def ler_destino(nc: Nextcloud, url: str, inicio: int, n: int) -> bytes:
    req = urllib.request.Request(url, headers={"Authorization": nc._auth, "Range": f"bytes={inicio}-{inicio + n - 1}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        if r.status != 206:
            raise IOError(f"servidor nao respeitou Range (HTTP {r.status})")
        dados = r.read()
    if len(dados) != n:
        raise IOError(f"leitura curta do destino: {len(dados)} de {n}")
    return dados


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--origem", action="store_true")
    ap.add_argument("--regioes", action="store_true")
    ap.add_argument("--blocos", type=int, nargs="*", default=[])
    ap.add_argument("--integral", action="store_true")
    a = ap.parse_args()
    con = conectar()
    it = con.execute("SELECT p.item_id, p.destino_pasta, p.nome_final, o.drive_id, o.tamanho, o.quick_xor_hash "
                     "FROM plano_item p JOIN origem_item o USING(item_id) WHERE p.lote='VIDEO-87G'").fetchone()
    total, qxh = it["tamanho"], it["quick_xor_hash"]
    nc = Nextcloud(_m346.BASE_DAV, _m346.UID, cfg()["destino"]["login"], _m346.senha_dpapi())
    url = nc.url_arquivo(f"PARCEIRO-INFRA/{it['destino_pasta']}/{it['nome_final']}")
    g = Graph()
    res: dict = {"quando": time.strftime("%Y-%m-%dT%H:%M:%S"), "esperado_bytes": total, "esperado_qxh": qxh}

    if a.origem:
        o = g.get(f"/drives/{it['drive_id']}/items/{it['item_id']}?$select=id,size,lastModifiedDateTime,file")
        res["origem_agora"] = {"bytes": o.get("size"), "qxh": ((o.get("file") or {}).get("hashes") or {}).get("quickXorHash"),
                               "modificado": o.get("lastModifiedDateTime")}
        res["origem_inalterada"] = o.get("size") == total and _mesmo_qxh(res["origem_agora"]["qxh"] or "", qxh)
        res["destino_bytes_propfind"] = nc.tamanho(url)
        print(json.dumps(res, ensure_ascii=False, indent=1), flush=True)

    if a.regioes:
        fonte = FonteGraph(g, it["drive_id"], it["item_id"], total)
        corte = 42719727816                       # onde a leitura do 337 parou
        pontos = {"inicio": 0, "fim": total - BLOCO_ENVIO,
                  "corte_337": corte - corte % BLOCO_ENVIO, "meio": (total // 2) - (total // 2) % BLOCO_ENVIO}
        for b in a.blocos:
            pontos[f"bloco_{b}"] = (b - 1) * BLOCO_ENVIO
        res["regioes"] = {}
        for nome, ini in pontos.items():
            n = min(BLOCO_ENVIO, total - ini)
            so = hashlib.sha256(fonte.ler(ini, n)).hexdigest()
            sd = hashlib.sha256(ler_destino(nc, url, ini, n)).hexdigest()
            res["regioes"][nome] = {"inicio": ini, "bytes": n, "sha_origem": so, "sha_destino": sd, "igual": so == sd}
            print(f"{nome:12} byte {ini:>12} : {'IGUAL' if so == sd else 'DIFERENTE'}", flush=True)
        (SAIDA / "video_conferencia_regioes.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    if a.integral:
        qx, sha, pos, t0, quedas = QuickXorHash(), hashlib.sha256(), 0, time.time(), 0
        prog = SAIDA / "video_conferencia_integral.json"
        while pos < total:
            req = urllib.request.Request(url, headers={"Authorization": nc._auth, "Range": f"bytes={pos}-"})
            try:
                with urllib.request.urlopen(req, timeout=300) as r:
                    if r.status != 206 and pos > 0:
                        raise IOError(f"servidor nao respeitou Range (HTTP {r.status})")
                    ultimo = time.time()
                    while pos < total:
                        b = r.read(8 * MiB)
                        if not b:
                            break
                        qx.update(b)
                        sha.update(b)
                        pos += len(b)
                        if time.time() - ultimo > 60:
                            ultimo = time.time()
                            v = pos / MiB / (time.time() - t0)
                            prog.write_text(json.dumps({"situacao": "LENDO", "lidos": pos, "total": total,
                                                        "pct": round(100 * pos / total, 2), "MiB_s": round(v, 2),
                                                        "quedas_retomadas": quedas,
                                                        "atualizado": time.strftime("%Y-%m-%dT%H:%M:%S")}), encoding="utf-8")
            except Exception as e:
                quedas += 1
                print(f"queda no byte {pos}: {e} - retomando do mesmo byte", flush=True)
                if quedas > 50:
                    raise
                time.sleep(min(10 * quedas, 120))
        fim = {"situacao": "CONCLUIDA", "lidos": pos, "total": total, "segundos": round(time.time() - t0),
               "quedas_retomadas": quedas, "qxh_destino": qx.base64(), "sha256_destino": sha.hexdigest(),
               "qxh_microsoft": qxh, "tamanho_ok": pos == total,
               "hash_ok": pos == total and _mesmo_qxh(qx.base64(), qxh),
               "atualizado": time.strftime("%Y-%m-%dT%H:%M:%S")}
        prog.write_text(json.dumps(fim, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(fim, ensure_ascii=False, indent=1))
        return 0 if fim["hash_ok"] else 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

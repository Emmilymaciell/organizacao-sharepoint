"""
349_migra_video_homologar - registra a conferencia integral do 348 e homologa o VIDEO-87G, SE e SOMENTE SE:

  1. o 348 leu integralmente o arquivo persistido no PARCEIRO (situacao CONCLUIDA, lidos == total);
  2. o tamanho lido == tamanho da Microsoft no inventario (94.227.687.480 B);
  3. o QuickXor integral do destino == quickXorHash da Microsoft (recalculado aqui, nao so o flag);
  4. a origem continua a do inventario (Graph agora: mesmo tamanho e mesmo QuickXor).

Entao (autorizacao condicional da Emmily, 21/10/2026): grava a linha em `verificacao`, muda o estado
para VALIDADO com evento, grava VIDEO_CONCLUIDO e desativa a tarefa de upload do video (sem processo
ativo). Nada e apagado: logs, checkpoints, VIDEO_PARADO e evidencias ficam. Se qualquer criterio
falhar, NAO marca nada e sai 4 (ou 3 se o 348 ainda nao terminou). Idempotente.

Uso:
    python scripts/349_migra_video_homologar.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from graph_util import Graph  # noqa: E402
from migra_parceiro.base import agora, conectar  # noqa: E402
from migra_parceiro.motor import _mesmo_qxh  # noqa: E402

EVIDENCIA = RAIZ / "output" / "migracao_parceiro" / "video_conferencia_integral.json"
TMP = RAIZ / "tmp" / "migracao_parceiro"
AUTORIZACAO = ('Emmily, 21/10/2026: "AUTORIZO CONDICIONALMENTE A HOMOLOGACAO TECNICA DO VIDEO-87G: Se, e somente '
               'se, o verificador 348: 1. Ler integralmente os 94.227.687.480 bytes do arquivo persistido no PARCEIRO; '
               '2. Confirmar que o tamanho corresponde exatamente ao arquivo original; 3. Calcular o QuickXorHash '
               'integral e confirmar igualdade exata com o hash informado pela Microsoft Graph; 4. Registrar '
               'evidencias verificaveis [...] entao AUTORIZO: Registrar essa conferencia integral no banco SQLite. '
               'Alterar o estado do video para VALIDADO."')


def main() -> int:
    con = conectar()
    it = con.execute("SELECT p.item_id, p.estado, p.destino_pasta, p.nome_final, o.drive_id, o.tamanho, "
                     "o.quick_xor_hash FROM plano_item p JOIN origem_item o USING(item_id) "
                     "WHERE p.lote='VIDEO-87G'").fetchone()
    if it["estado"] == "VALIDADO":
        print("ja homologado (VALIDADO)")
        return 0
    if not EVIDENCIA.exists():
        print("348 ainda nao gerou evidencia")
        return 3
    ev = json.loads(EVIDENCIA.read_text(encoding="utf-8"))
    if ev.get("situacao") != "CONCLUIDA":
        print(f"348 ainda lendo: {ev.get('pct')}%")
        return 3
    o = Graph().get(f"/drives/{it['drive_id']}/items/{it['item_id']}?$select=id,size,file")
    qx_origem = ((o.get("file") or {}).get("hashes") or {}).get("quickXorHash") or ""
    criterios = {
        "1_leitura_integral": ev["lidos"] == ev["total"] == it["tamanho"],
        "2_tamanho_igual_microsoft": ev["lidos"] == it["tamanho"] == 94227687480,
        "3_quickxor_igual_microsoft": _mesmo_qxh(ev["qxh_destino"], it["quick_xor_hash"]),
        "4_origem_inalterada": o.get("size") == it["tamanho"] and _mesmo_qxh(qx_origem, it["quick_xor_hash"]),
    }
    print(json.dumps({"evidencia": ev, "criterios": criterios}, ensure_ascii=False, indent=1))
    if not all(criterios.values()):
        print("NAO HOMOLOGADO: criterio falhou; nada foi alterado")
        return 4
    destino = f"{it['destino_pasta']}/{it['nome_final']}"
    q = agora()
    det = (f"conferencia integral 348 (WebDAV direto, Range retomavel): {ev['lidos']} B lidos em {ev['segundos']} s, "
           f"{ev['quedas_retomadas']} quedas retomadas; qxh destino {ev['qxh_destino']} == Microsoft "
           f"{it['quick_xor_hash']}; sha256 {ev['sha256_destino']}; evidencia {EVIDENCIA.name} ({ev['atualizado']}). "
           f"Substitui o DIVERGENTE de 21/10 00:43 (leitura do 337 cortada em 1 h pelo rcd, 42.719.727.816 B). "
           f"Autorizacao: {AUTORIZACAO}")
    con.execute("INSERT INTO verificacao (item_id, quando, caminho_destino, bytes_destino, qxh_destino, sha256_destino, "
                "sha256_origem, metodo, resultado, detalhe) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (it["item_id"], q, destino, ev["lidos"], ev["qxh_destino"], ev["sha256_destino"], None,
                 "qxh_servidor_x_destino_integral_348", "VALIDADO", det))
    con.execute("UPDATE plano_item SET estado='VALIDADO', atualizado_em=? WHERE item_id=?", (q, it["item_id"]))
    con.execute("INSERT INTO evento (item_id, quando, de_estado, para_estado, detalhe) VALUES (?,?,?,?,?)",
                (it["item_id"], q, it["estado"], "VALIDADO", det))
    con.commit()
    (TMP / "VIDEO_CONCLUIDO").write_text(q, encoding="ascii")
    print(f"VIDEO-87G VALIDADO em {q}")
    vivo = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                           "@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object "
                           "{ $_.CommandLine -like '*346_migra_video*' }).Count"],
                          capture_output=True, text=True).stdout.strip()
    if vivo == "0":
        for tarefa in ("Programa Migracao PARCEIRO - Video", "Programa Migracao PARCEIRO - Homologar Video"):
            subprocess.run(["schtasks", "/Change", "/TN", tarefa, "/DISABLE"], capture_output=True)
        print("tarefas do video desativadas (nao excluidas)")
    else:
        print(f"ATENCAO: {vivo} processo(s) 346 ativos; tarefa do video NAO desativada")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

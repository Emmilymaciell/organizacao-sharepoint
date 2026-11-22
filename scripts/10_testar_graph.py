"""
10_testar_graph — confere, em 30 segundos, se o acesso via API esta funcionando.

    python scripts\\10_testar_graph.py

Faz apenas LEITURA. Nao baixa, nao altera, nao apaga nada.

Testa, em ordem, e para no primeiro que falhar — dizendo exatamente o que
pedir para a TI:

    1. autenticacao (token)
    2. o aplicativo enxerga o site do Programa
    3. quais permissoes o aplicativo tem NESTE site
    4. lista as bibliotecas de documentos
    5. lista as primeiras pastas da biblioteca Programa
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import graph_util as gu  # noqa: E402


def cabecalho(texto: str):
    print("\n" + "=" * 66)
    print(texto)
    print("=" * 66)


def main() -> int:
    cabecalho("TESTE DE ACESSO — Microsoft Graph / SharePoint Programa")

    try:
        graph = gu.Graph()
    except ValueError as exc:
        print(f"  ERRO: {exc}")
        print("  Preencha o arquivo .env na raiz do repositorio.")
        return 2

    print(f"  tenant   {graph.env['GRAPH_TENANT_ID']}")
    print(f"  client   {graph.env['GRAPH_CLIENT_ID']}")
    print(f"  site     https://{graph.host}{graph.site}")

    # 1 ---------------------------------------------------------------
    print("\n[1] autenticacao...")
    try:
        graph.token()
        print("    OK — credencial aceita pelo Entra ID")
    except gu.ErroGraph as erro:
        print(f"    FALHOU — {erro}")
        print("\n" + gu.explicar(erro))
        return 1

    # 2 ---------------------------------------------------------------
    print("\n[2] enxergar o site do Programa...")
    try:
        site = graph.get(f"/sites/{graph.host}:{graph.site}")
        site_id = site["id"]
        print(f"    OK — {site.get('displayName') or site.get('name')}")
        print(f"    site id: {site_id}")
    except gu.ErroGraph as erro:
        print(f"    FALHOU — {erro}")
        print("\n" + gu.explicar(erro))
        print("\nO QUE PEDIR PARA A TI (Etapas 3 e 4 do anexo):")
        print("  1. Entra ID > o aplicativo > Permissoes de API > Microsoft Graph >")
        print("     Permissoes de APLICATIVO > Sites.Selected > Conceder consentimento")
        print("     do administrador.")
        print("  2. Liberar o aplicativo NESTE site, com role 'write' — pelo Graph")
        print("     Explorer:")
        print("     POST https://graph.microsoft.com/v1.0/sites/"
              f"{graph.host}:{graph.site}:/permissions")
        print('     {"roles":["write"],"grantedToIdentities":[{"application":'
              f'{{"id":"{graph.env["GRAPH_CLIENT_ID"]}",'
              '"displayName":"Programa - App Graph"}}]}')
        return 1

    # 3 ---------------------------------------------------------------
    print("\n[3] permissoes do aplicativo neste site...")
    try:
        permissoes = graph.permissoes_do_site(site_id)
        if not permissoes:
            print("    nenhuma permissao especifica listada")
        for permissao in permissoes:
            papeis = ",".join(permissao.get("roles", []))
            for identidade in permissao.get("grantedToIdentities", []) or []:
                app = identidade.get("application", {})
                marca = "  <-- este e o nosso app" if app.get("id") == graph.env[
                    "GRAPH_CLIENT_ID"] else ""
                print(f"    [{papeis}] {app.get('displayName')} ({app.get('id')}){marca}")
    except gu.ErroGraph as erro:
        print(f"    nao consegui listar ({erro.codigo}) — segue o teste")

    # 4 ---------------------------------------------------------------
    print("\n[4] bibliotecas de documentos...")
    try:
        drives = graph.bibliotecas(site_id)
        for drive in drives:
            cota = drive.get("quota", {}) or {}
            usado = (cota.get("used") or 0) / 1024 ** 3
            print(f"    {drive.get('name'):<28} {usado:>8.1f} GB usados   id={drive['id'][:24]}…")
    except gu.ErroGraph as erro:
        print(f"    FALHOU — {erro}")
        print("\n" + gu.explicar(erro))
        return 1

    # 5 ---------------------------------------------------------------
    alvo = next((d for d in drives if (d.get("name") or "").upper() == "PROGRAMA"), None)
    if alvo is None:
        alvo = drives[0] if drives else None
    if alvo is None:
        print("\n    nenhuma biblioteca encontrada")
        return 1

    print(f"\n[5] primeiras pastas de '{alvo['name']}'...")
    try:
        itens = graph.get(f"/drives/{alvo['id']}/root/children?$top=25"
                          "&$select=name,size,folder,file,lastModifiedDateTime")
        for item in itens.get("value", []):
            tipo = "PASTA " if "folder" in item else "arquivo"
            tamanho = (item.get("size") or 0) / 1024 ** 2
            print(f"    {tipo}  {item['name'][:52]:<52} {tamanho:>9.1f} MB")
    except gu.ErroGraph as erro:
        print(f"    FALHOU — {erro}")
        print("\n" + gu.explicar(erro))
        return 1

    cabecalho("TUDO CERTO — o acesso via API esta funcionando")
    print(f"  site id   {site_id}")
    print(f"  drive id  {alvo['id']}")
    print("\n  Proximo passo: inventariar pela nuvem, sem sincronizar nada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

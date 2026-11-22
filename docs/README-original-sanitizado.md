# Programa — Organização do SharePoint

Diagnóstico e limpeza do repositório do contrato Programa, que **estourou o limite
de armazenamento**. Entrega prevista: **1º de outubro de 2026**.

> **Fase atual: ANÁLISE — somente leitura.**
> Nada é apagado, movido, copiado ou renomeado. O projeto produz a lista do que
> pode sair; a execução vem depois, sobre o que for aprovado.

---

## O acervo

| | |
|---|---|
| Site | `https://contoso.sharepoint.com/sites/exemplo` |
| Biblioteca | `Programa` — 6 pastas de primeiro nível |
| Arquivos | ~139 mil |
| Consumo reportado | **1.384 GB** |
| Acesso | Microsoft Graph API (app `Programa - App Graph`) |

A diferença entre o consumo reportado (1.384 GB) e o volume de arquivos vivos
(~1.168 TB medido em varredura local) é a primeira pista do projeto: parte
relevante do espaço pode estar em **histórico de versões e lixeira** — espaço
recuperável sem apagar nenhum documento.

---

## As três entregas

| # | Entrega | Resultado |
|---|---|---|
| 1 | **Inventário pela nuvem** (`delta query`) | todo item do site: caminho, tamanho, datas, autor e **hash**, sem baixar nada |
| 2 | **Duplicatas exatas** | grupos com o mesmo `quickXorHash`, quanto espaço representam e onde estão |
| 3 | **Análise de consumo** | onde está o 1,3 TB, quanto é versão antiga, quanto é lixeira, quanto é arquivo pesado sem valor documental |

Só depois disso se discute o que apagar.

---

## Por que pela nuvem, e não pela pasta sincronizada

O SharePoint devolve pela API o **hash de cada arquivo já calculado pelo
servidor**. Isso permite identificar duplicatas exatas do acervo inteiro **sem
baixar 1 TB** — o que inviabilizaria o prazo. A pasta sincronizada, além de
lenta, não enxerga versões nem lixeira.

---

## Uso

```bash
cd "C:\caminho\exemplo\organizacao-sharepoint"

python scripts\10_testar_graph.py      # confere o acesso (30 s, só leitura)
```

Os demais scripts são criados conforme o plano em [`docs/01_PLANO.md`](../docs/01_PLANO.md).

---

## Estrutura

```
PROG_Organizacao_SharePoint/
├── config/projeto.yaml     # alvo, limites e a TRAVA DE FASE (analise/execucao)
├── scripts/
│   ├── graph_util.py       #   cliente do Microsoft Graph (só biblioteca padrão)
│   └── 10_testar_graph.py  #   teste de acesso ponta a ponta
├── sql/                    # esquema do banco
├── data/                   # SQLite (fora do Git)
├── output/                 # relatórios e planilhas para revisão
├── docs/                   # plano, decisões, achados
├── logs/ · state/ · tmp/ · tests/
└── .env                    # credenciais — FORA DO GIT
```

---

## Regras que não se negociam

1. **Fase de análise é somente `GET`.** Qualquer escrita exige autorização
   explícita, por escrito, naquela conversa.
2. **Nunca usar a pasta sincronizada local** como fonte.
3. **Credenciais só no `.env`** — nunca em código, log, relatório ou commit.
4. **Duplicata é hash igual**, não nome parecido.
5. **Varredura retomável** — queda não recomeça do zero.

Detalhes em [`CLAUDE.md`](../CLAUDE.md).

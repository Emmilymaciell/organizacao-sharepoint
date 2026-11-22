# Programa — Organização do SharePoint · regras do repositório

Projeto de **diagnóstico e organização** do SharePoint do contrato Programa.
Prazo da entrega: **1º de outubro de 2026**.

---

## ⛔ REGRA Nº 1 — FASE ATUAL É SOMENTE LEITURA

Nesta fase o projeto **apenas analisa**. É proibido, no SharePoint:

- apagar, mover, copiar, renomear, sobrescrever ou criar qualquer item;
- restaurar ou esvaziar lixeira;
- apagar versões de documentos;
- alterar permissões, colunas, metadados ou configurações do site.

Todo método do Graph que não seja `GET` exige **autorização explícita da
Emmily, por escrito, naquela conversa**. Não vale autorização genérica de
mensagens anteriores.

A limpeza será executada depois, em fase própria, sobre uma lista aprovada.

---

## ⛔ REGRA Nº 2 — TRABALHO É 100% PELA NUVEM

O acesso é via **Microsoft Graph API**, com as credenciais do `.env`.

- **Não usar a pasta sincronizada** `C:\caminho\exemplo\sincronizada\`.
  Ela é lenta, incompleta (Arquivos Sob Demanda) e não reflete versões nem lixeira.
- **Não baixar arquivos em massa.** O Graph entrega nome, tamanho, datas, autor
  e **hash (`quickXorHash`) sem baixar o conteúdo** — é isso que torna o
  diagnóstico viável no prazo.
- Download só de arquivo isolado, quando houver motivo claro, e para `tmp/`.

---

## ⛔ REGRA Nº 3 — CREDENCIAIS

Ficam em `.env`, na raiz, **fora do Git**. Nunca colar o segredo em código,
log, relatório, commit, issue ou mensagem. O `.gitignore` já bloqueia `.env`,
`*.pfx`, `*.cer` e `*.key`.

O aplicativo (`Programa - App Graph`) tem hoje `Sites.Selected` **e**
`Sites.ReadWrite.All` — ou seja, tecnicamente consegue escrever. É justamente
por isso que a Regra nº 1 existe: o limite é de processo, não da permissão.

---

## REGRA Nº 4 — PROGRAMAÇÃO PRIMEIRO

```
código determinístico → extração estruturada → indexação → heurística → IA
```

Duplicata é **hash igual**, não nome parecido. Consumo é **soma de bytes**, não
estimativa. Nenhuma conclusão sem o número que a sustenta.

Nesta fase não há chamada de API de IA sobre os documentos, e nenhum documento
sai desta máquina.

---

## REGRA Nº 5 — NADA SOME DO CATÁLOGO

Formato desconhecido, arquivo com erro, item inacessível: tudo é registrado com
o motivo. Um item problemático nunca interrompe a varredura.

---

## REGRA Nº 6 — RASTREABILIDADE

Toda linha de relatório aponta para: `item_id` do Graph + caminho completo +
hash + tamanho + data. Quem ler o relatório tem que conseguir abrir o arquivo e
conferir.

---

## REGRA Nº 7 — RETOMÁVEL

Varredura de 139 mil itens não pode recomeçar do zero por causa de uma queda.
Estado em SQLite, commit por lote, `deltaLink` salvo. Se cair, retoma.

---

## Ambiente

| | |
|---|---|
| Tenant | `contoso.sharepoint.com` (Entra ID: Programa) |
| Site | `https://contoso.sharepoint.com/sites/exemplo` |
| Biblioteca | `Programa` — 6 pastas de primeiro nível |
| Volume | ~139 mil arquivos · **1.384 GB de consumo reportado** |
| Python | 3.12, biblioteca padrão (o `graph_util` não usa dependência externa) |

---

## Convenções

- Scripts numerados por ordem de execução, em `scripts/`.
- Banco em `data/`, relatórios em `output/`, logs em `logs/` — tudo fora do Git.
- `STATE.md` é a verdade sobre onde o projeto está. Manter atualizado.
- Idioma: português do Brasil.

---

## Sessões

- **`/session-start`** — protocolo oficial de abertura. Reconstrói o estado
  pelo repositório, reconcilia com Git e disco, e **para** antes de implementar.
- **`/session-close`** — protocolo oficial de encerramento. Persiste estado,
  decisões, achados e o registro da sessão, e **para**.
- Onde cada informação mora: [`docs/00_CONTINUIDADE.md`](docs/00_CONTINUIDADE.md).

---

## Versionamento

Commit **só quando a Emmily pedir** ("salva essa versão", "commita",
`/versionar`). Nunca `push`, `reset --hard`, `clean -fd` ou reescrita de
histórico.

Fora do Git: `.env`, `data/*.db`, `logs/`, `tmp/`, downloads.

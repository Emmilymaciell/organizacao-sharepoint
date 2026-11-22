# Plano — da análise à limpeza

Cada etapa tem **entregável** e **teste objetivo**. Etapa sem teste passado não
avança. Prazo da entrega final: **1º de outubro de 2026**.

---

## E0 — Acesso ✅

| | |
|---|---|
| Entregável | `scripts/graph_util.py` + `scripts/10_testar_graph.py` |
| Teste | `python scripts\10_testar_graph.py` passa nos 5 checks |
| Validado | autenticação, site, 6 bibliotecas, listagem, e **hash sem download** |

Permissões concedidas ao app: `Sites.Selected` + `Sites.ReadWrite.All`.
A escrita está tecnicamente liberada — e permanece **travada por processo**
(`config/projeto.yaml`: `permitir_escrita: false`).

---

## E1 — Inventário completo pela nuvem

| | |
|---|---|
| Como | `GET /drives/{id}/root/delta`, paginado, uma passada por biblioteca |
| Coleta | `item_id`, caminho, nome, extensão, tamanho, criado/modificado, autor, `quickXorHash`, `mimeType`, `webUrl`, `eTag` |
| Grava | tabela `item_nuvem` no SQLite + `deltaLink` para o incremental |
| Retomada | `deltaLink`/`nextLink` salvos; queda não recomeça do zero |
| Estimativa | 5–10 min para ~139 mil itens |

**Teste:** o total de arquivos e a soma de bytes batem, em ordem de grandeza,
com o consumo que o SharePoint reporta (1.384 GB) e com a varredura local
(139.459 arquivos). Divergência grande = investigar antes de seguir.

⚠️ Nenhum arquivo é baixado nesta etapa.

---

## E2 — Duplicatas exatas

| | |
|---|---|
| Critério | **mesmo `quickXorHash`** (conteúdo idêntico byte a byte) — nunca nome parecido |
| Entregável | `output/duplicatas.csv` e `output/duplicatas.md` |
| Conteúdo | grupo, nº de cópias, tamanho unitário, **espaço recuperável**, caminho de cada cópia, qual é a mais antiga |
| Regra | o relatório **sugere** qual cópia manter (a mais antiga, ou a da pasta mais "oficial"), mas **não decide** |

**Teste:** abrir 3 grupos na mão no SharePoint e confirmar que são mesmo
idênticos.

---

## E3 — Análise de consumo

Responder, com número:

1. **Onde está o 1,3 TB** — consumo por pasta de primeiro e segundo nível.
2. **Quanto é versão antiga** — `GET /items/{id}/versions` nos maiores arquivos;
   o histórico conta na cota e costuma ser a maior surpresa.
3. **Quanto é lixeira** — itens excluídos que ainda ocupam espaço.
4. **Quanto é arquivo pesado sem valor documental** — `.zip`, `.dav`, vídeo,
   `.bak`, `.iso`.
5. **Quanto é material superado** — pastas com padrão de descarte no nome.

| | |
|---|---|
| Entregável | `output/consumo.md` + `output/consumo_por_pasta.csv` |
| Teste | a soma das categorias explica o consumo reportado, com folga pequena |

---

## E4 — Lista de limpeza (proposta)

Consolida E2 e E3 numa **lista priorizada** do que pode sair, ordenada por
espaço recuperado ÷ risco:

1. versões antigas além do limite (não apaga documento)
2. lixeira
3. duplicatas exatas fora das pastas oficiais
4. arquivos pesados sem valor documental
5. pastas de material superado

Cada item com: caminho, tamanho, motivo, e **onde existe a outra cópia**.

**A Emmily revisa e aprova.** Nada é executado nesta etapa.

---

## E5 — Execução (fase própria, só depois da aprovação)

Só começa quando:

- a lista estiver aprovada por escrito;
- `config/projeto.yaml` mudar para `fase: execucao` e `permitir_escrita: true`;
- existir **modo simulação** (`--simular`) mostrando exatamente o que seria feito;
- existir registro de tudo que for executado (caminho, tamanho, hash, data).

Ordem de execução, do mais seguro para o menos: versões → lixeira →
duplicatas → arquivos pesados → superados.

---

## O que NÃO está no escopo deste projeto

Indexação de conteúdo, busca semântica e camada de IA são do projeto
**Cérebro Documental** (`C:\caminho\exemplo\PROG_Cerebro_Documental`).
Os dois compartilham o mesmo acesso, mas têm objetivos e repositórios
separados.

# STATE — Estado atual (versão pública)

> Versão sanitizada do `STATE.md` do repositório privado (snapshot de 11/11/2026, sessão S09).
> O original é um diário operacional com nomes de pastas, documentos e pessoas do contrato; aqui
> ficam só os números e o estado técnico. Rodovias, órgãos e parceiros foram substituídos por
> identificadores genéricos (RD-101…RD-104, Órgão Contratante, Parceiro). **Dados fictícios na
> pasta `examples/`; nenhum dado real acompanha este repositório.**

## Situação

**Objetivo do projeto atingido em 02/10/2026:** a cota do site ficou abaixo do limite.

| Medida | Valor | Fonte (no repositório privado) |
|---|---:|---|
| Cota usada em 21/09 (início) | 1.384,20 GiB | GET `quota.used` |
| Cota usada em 04/10 | **861,14 GiB** (84,1%) | GET `quota.used` |
| Limite do site | 1.024 GiB | informado pela autora |
| Folga em 04/10 | 162,86 GiB | |
| Lixeira em 04/10 | 0,00 GiB | GET `quota.deleted` |

## Estado de processamento

| Frente | Feito |
|---|---|
| Catálogo pela nuvem (E1, `delta query`) | varredura concluída em 21/09 · 138.344 arquivos |
| Exclusões no SharePoint (com autorização escrita) | 158 pastas (30/09) + 62 pastas + 2 vídeos (01–02/10), cada item registrado em log restaurável |
| Poda de versões | 620 arquivos · 4.654 versões · 108,49 GiB |
| Backup da RD-102A antes de apagar | 2.313 / 2.313 arquivos (275.831.036.044 B), conferidos por quantidade + bytes |
| Migração da pasta 5 → Nextcloud do Parceiro | 16.403 enviados · **16.402 VALIDADO** (131,68 GiB) por hash relido do destino; 1 vídeo de 87,76 GiB montado sem validação integral, por decisão registrada |
| Migração G2 → Nextcloud do Parceiro (4 rodovias) | **12.795 VALIDADO** · 0 erro |
| Pacotes ZIP para o GED do Órgão (11/11) | 63 pastas · 410 arquivos → 70 ZIPs, 70/70 validados (CRC + SHA-256 por membro × original) |

## Pendências (resumidas)

- P-3/P-4 · catálogo de 21/09 desatualizado; poda de versões dos arquivos < 10 MB não varrida.
- P-5 · representar a fase de execução no `config/projeto.yaml` sem fazer os scripts de análise abortarem.
- P-7 · migração da pasta 5: decisão da chefia sobre a ressalva do vídeo e a limpeza da origem.
- P-9 · pendências estruturais da G2 (pastas recusadas pelo servidor com 403, script 367 que recria pastas).

## Última validação

| Quando | Comando | Resultado |
|---|---|---|
| 11/11 | `python -m pytest -q tests` · `py_compile scripts/390_ged_zip_ctd.py` | 137 passed · ok |
| 11/11 | `390 validar` | 70/70 OK · 410/410 cobertos · 0 faltando · 0 original alterado |
| 05/11 | envio de 61 arquivos da RD-104 | 61/61 VALIDADO (SHA-256 + QuickXor) |
| 28/10 | `365_g2_testar_motor.py` (Nextcloud real, pasta de teste) | 11/11 |
| 28/10 | `371_g2_auditoria.py` + `372` (amostra relida do destino) | 7/7 checagens; 69/69 PASS |
| 20/10 | auditoria de presença no destino | 16.402/16.402 presentes, 0 tamanho diferente |

## Na versão pública

- Suíte de testes: **pendente** (roda em série depois da sanitização).
- Demo offline: `examples/carregar_inventario_ficticio.py` + `scripts/30_duplicatas.py --min-bytes 0`.
- Documentos operacionais do original (`docs/03_ACHADOS.md`, `docs/04_SESSOES.md`, handoffs,
  históricos e pendências da G2) foram removidos por conterem dado do contrato; links para eles
  nos documentos restantes ficam quebrados de propósito.

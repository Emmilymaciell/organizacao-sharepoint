# Operação G2 → PARCEIRO INFRA — comandos

Operação `migracao_parceiro_g2` (D-11, D-12). Rode tudo na raiz do projeto
`C:\caminho\exemplo\organizacao-sharepoint`, no PowerShell.

> **O G2 é SECUNDÁRIO** (decisão da Emmily, 29/10/2026). Outro projeto no mesmo notebook
> (`C:\caminho\exemplo\work\prog\entregas-finais-projeto-prog`) tem prioridade absoluta. O executor só envia com
> capacidade ociosa: o **governador de recursos** reduz, pausa e retoma sozinho. Ficar pausado por horas
> **não é falha** — é o estado `PAUSADO_POR_PRIORIDADE_EXTERNA`.

## O que depende do terminal

| Peça | Depende do terminal? |
|---|---|
| Executor `367_g2_executar.py` lançado pela tarefa **Programa Migracao PARCEIRO G2 - FILA** | **Não.** Filho do Agendador. Pode fechar o Claude e o terminal. |
| A tarefa repete a cada 15 min (watchdog) | Não. Exige o notebook **ligado, com sessão aberta e internet**. Desligado, nada roda. |
| Painel, relatórios, planejamento, auditorias | Sim. Rodam e terminam. |

## Comandos

| Quero | Comando |
|---|---|
| Progresso geral (4 rodovias) + recursos | `python scripts\368_g2_status.py` |
| Progresso de uma rodovia | `python scripts\368_g2_status.py --rodovia RD-101` |
| Conflitos (pulados) de uma rodovia | `python scripts\368_g2_status.py --rodovia RD-101 --conflitos` |
| Falhas de uma rodovia | `python scripts\368_g2_status.py --rodovia RD-101 --falhas` |
| Recursos e **motivo da pausa** (histórico do governador) | `python scripts\368_g2_status.py --recursos` |
| Estado do governador agora | `Get-Content tmp\migracao_parceiro_g2\GOVERNADOR.json` |
| Arquivos transferidos de uma rodovia | `output\migracao_parceiro_g2\relatorios_<GO>\ARQUIVOS_TRANSFERIDOS_G2.csv` (gerar com o `370`) |
| Documentos que já existiam (estagiários/terceiros) | `output\migracao_parceiro_g2\auditoria_<GO>\10_PREEXISTENTES_PARCEIRO.csv` (gerar com o `375`) |
| Divergências para decisão (4 rodovias) | `output\migracao_parceiro_g2\DECISOES_HUMANAS_G2_4_RODOVIAS.csv` |
| Acompanhar o log ao vivo | `Get-Content logs\migracao_parceiro_g2\LOG_OPERACIONAL_G2.log -Tail 20 -Wait -Encoding UTF8` |
| **Pausar** (termina os envios ativos e para) | `New-Item tmp\migracao_parceiro_g2\PAUSAR` |
| **Retomar** | `Remove-Item tmp\migracao_parceiro_g2\PAUSAR; Start-ScheduledTask "Programa Migracao PARCEIRO G2 - FILA"` |
| Desativar a tarefa (pausa longa / fim) | `powershell -File scripts\369_g2_rodar.ps1 -Rodovias "RD-102A,RD-104,RD-101" -Remover` |
| Reinstalar a tarefa | `powershell -File scripts\369_g2_rodar.ps1 -Rodovias "RD-102A,RD-104,RD-101" -Instalar` |
| Gerar relatórios de uma rodovia | `python scripts\370_g2_relatorios.py --rodovia RD-101` |
| Auditar preexistentes de uma rodovia | `python scripts\375_g2_preexistentes.py --rodovia RD-101 --foto-base <N>` |
| Foto do PARCEIRO / alterações de terceiros | `python scripts\362_g2_foto_parceiro.py --tipo monitor` |
| Último backup | `Get-ChildItem backup\migracao_parceiro_g2 \| Sort-Object LastWriteTime \| Select-Object -Last 3` |

Relatórios: `output\migracao_parceiro_g2\` (fora do Git). Log: `logs\migracao_parceiro_g2\`.

## Governador de recursos (`scripts\migra_g2\governador.py`, bloco `recursos` do yaml)

| Estado | Quando | Workers |
|---|---|---|
| `AGUARDANDO_RECURSOS` | ao iniciar e depois de pausa, até 5 min seguidos de folga | 0 |
| `RETOMANDO` | após 5 min estáveis com RAM ≥ 4 GiB e CPU < 60% | 1 |
| `EXECUTANDO` | mais 5 min estáveis; sobe 1 worker por janela estável até `workers_max` (6 desde 30/10, pedido da Emmily) | 1–6 |
| `REDUZINDO_CARGA` | RAM entre 3 e 4 GiB, ou CPU média ≥ 70% | 1 |
| `PAUSA_POR_RECURSOS` | RAM < 3 GiB, commit ≥ 90%, CPU média ≥ 90% ou disco < 8 GiB | 0 |
| `PAUSADO_POR_PRIORIDADE_EXTERNA` | a mesma pressão com o projeto prioritário ativo | 0 |
| `PAUSADO_MANUALMENTE` | arquivo `PAUSAR` | 0 |

- **Limiares vigentes (yaml, desde 29/10 11:5x, pedido da Emmily "se der 98% para"):** pausa com uso de RAM ≥ 98%
  (`pausar_abaixo_mb: 330`), 1 worker com uso ≥ 95% (`reduzir_abaixo_mb: 800`), retoma acima de 800 MB livres estáveis;
  CPU 70/90/60%. Os valores em GiB da tabela acima são os originais de 29/10 manhã.
- Pausa dura no mínimo 5 min (sem oscilar). Os envios em andamento terminam; nada novo começa.
- Pausado por mais de 15 min, o executor **sai** (código 3, retomável) e libera a memória; a tarefa relança em 15 min.
- O processo roda em **BelowNormal + modo background** (E/S e memória em prioridade baixa); a tarefa, em prioridade 7.
- Medição: só leitura, ~20 ms por leitura, sem WMI. O projeto prioritário é reconhecido pelo caminho do
  executável e dos processos filhos. **Nenhum processo de terceiro é alterado, priorizado ou encerrado.**
- Tarefas leves de análise (`362`, `363`, `366`, `375`) também rodam em BelowNormal e esperam enquanto a pressão for alta.
- Cada mudança de estado e uma linha por minuto vão para a tabela `recurso` do banco (RAM, commit, CPU, consumo do
  prioritário, workers, progresso).

## Garantias do executor

- Um só executor: trava de arquivo `tmp\migracao_parceiro_g2\EXECUTOR.lock` (o segundo sai com código 6). A tarefa
  FILA processa as rodovias **uma de cada vez**.
- **Piloto obrigatório:** em `fase: execucao`, cada rodovia roda primeiro o seu lote PILOTO; a fila completa só
  segue se o piloto terminar sem ERRO/DIVERGENTE. Em `fase: piloto`, só o lote PILOTO roda.
- Retoma do banco: queda, reinício ou kill no meio de um envio não duplicam nem perdem (testado em 28/10).
- Antes de cada arquivo: origem igual à planejada, pasta de destino com o mesmo `oc:fileid`, destino ausente.
- Escrita só condicional (`If-None-Match: *` / `Overwrite: F`). Nada é apagado, movido, renomeado ou sobrescrito.
- A cada 20 min (com recursos livres) para de iniciar envios, espera os ativos, fotografa o PARCEIRO e só então retoma;
  se algum arquivo criado por nós sumir ou mudar de lugar, ou se a foto falhar, **pausa sozinho**.
- Trava de escopo no código: só `rodovias_liberadas`; Eventograma nunca; RPFO/DUP só pasta; Fase Executória só
  RD-102A; Documentação Supervisora nunca.
- Conteúdo idêntico (QuickXor) já presente em qualquer pasta da mesma rodovia: **não copia** (JA_EXISTIA_EM_OUTRO_DESTINO).

## Planejamento (o que entra)

`python scripts\366_g2_planejar.py --rodovia <GO>` — regras genéricas das 4 rodovias (docstring do script).
A RD-103 só admite `--simular` (o plano dela é histórico auditado), salvo `--refazer-rd103` (usado em 30/10 para a regra
de última revisão; replanejar apaga só linhas PLANEJADO, nunca reclassifica o que já foi executado). RD-102A e RD-103:
`TODAS_APROVADAS` → última REV de cada disciplina (D-13). Regressão de 29/10: o planejador genérico
reproduziu a classificação gravada da RD-103 com **0 diferenças em 23.239 linhas**.

## Scripts posteriores (29–31/10, D-12/D-13)

| Script | Faz | Escreve no PARCEIRO? |
|---|---|---|
| `375_g2_preexistentes.py --rodovia <GO> --foto-base <N>` | audita preexistentes × EAP | não |
| `376_g2_consolidado.py` | consolidado das 4 rodovias + `DECISOES_HUMANAS_G2_4_RODOVIAS.csv` | não |
| `377_g2_excluir_bm_fora_do_periodo.py` | corte de BM (`bm_ate` no yaml) | só com `--executar` + trava de fase |
| `378_g2_excel_entregaveis.py` | `EAP_G2_NOVA_ARQUITETURA.xlsx` e `INDICE_ENTREGAVEIS_G2.xlsx` (recalcular no Excel via COM em `%TEMP%`) | não |
| `379_g2_reorganizar_preexistentes.py [--executar] [--corrigir]` | move preexistentes para a EAP (MOVE `Overwrite: F`) | só com `--executar` + trava de fase |
| `380_g2_word_com_pdf.py [--executar]` | Word com PDF: exclui (fora de BM); lista os demais | **só `--executar` — sem trava de fase (PD-18)** |
| `381_g2_pastas_vazias.py [--executar] [--foto N]` | remove pastas vazias; lista duplicadas | **só `--executar` — sem trava de fase (PD-18)** |
| `382_g2_excluir_copias.py [--executar]` | exclui cópias comprovadas por QuickXor | **só `--executar` — sem trava de fase (PD-18)** |
| `383_g2_inventario_parcial.py "<subpasta>" …` | inventário parcial da origem (Graph GET) | não |

Sem argumento `--executar` os escritores só simulam e gravam `*_SIMULACAO.csv`.

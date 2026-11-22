# Continuidade entre sessões

Referência das skills `/session-start` e `/session-close`. Lida sob demanda,
não em toda sessão.

Princípio: **a conversa é descartável; o repositório é a fonte canônica.**
Git, disco e logs prevalecem sobre qualquer resumo escrito.

---

## 1. Onde cada coisa mora

| Função | Arquivo | Natureza |
|---|---|---|
| Regras permanentes | `CLAUDE.md` | muda raramente; autoridade máxima do projeto |
| Estado atual | `STATE.md` | **snapshot** — só o presente; reescrito a cada fechamento |
| Roteiro (E0–E5) | `docs/01_PLANO.md` | plano original; não é diário |
| Decisões duráveis | `docs/02_DECISOES.md` | `D-NN`; só se acrescenta, não se reescreve |
| Achados técnicos e de domínio | `docs/03_ACHADOS.md` | o que foi medido ou descoberto e continua verdadeiro |
| Histórico de sessões | `docs/04_SESSOES.md` | `SNN`; entrada nova no topo; compacta |
| Commit | skill `versionar` (usuário) | **só a pedido explícito** |
| Handoff para outra pessoa/máquina | skill `os:handoff` (plugin) | gera `docs/handoff/` quando pedido |

Não existe arquivo de handoff entre sessões: o "para onde ir" fica em
`STATE.md › Retomada` (atual) e o "o que aconteceu" em `docs/04_SESSOES.md`.

### Checkpoints de processamento (evidência, não resumo)

| Frente | Checkpoint | Como ler (somente leitura) |
|---|---|---|
| Catálogo E1 | tabela `varredura` em `data/organizacao.db` (`estado`, `delta_link`, `fim`) | consulta SQLite `mode=ro` |
| Itens catalogados | tabela `item_nuvem` (`visto_em` = data da varredura) | `COUNT(*)`, `MAX(visto_em)` |
| Exclusões no SharePoint | `logs/lixeira.csv` (`item_id` por linha — restaura) | contar `resultado=OK` |
| Poda de versões | `logs/versoes_apagadas.csv` | contar `resultado` |
| Backup RD102A | `output/rclone/rd102a_manifesto.csv` (hash e bytes por arquivo) | soma de `bytes` |
| Fotografia da cota | `data/posicao_atual.json` | só referência histórica |

`data/` e `logs/*.log` ficam fora do Git: existem nesta máquina, não num clone.
Num clone novo, o banco não existe — isso não é erro, é limitação conhecida.

---

## 2. Ordem de leitura na abertura

Pare assim que tiver o suficiente.

1. `CLAUDE.md` (já carregado pelo Claude Code)
2. `STATE.md` inteiro — é curto por desenho
3. Git: branch, HEAD, status, últimos commits
4. A entrada mais recente de `docs/04_SESSOES.md`
5. Só se a próxima tarefa exigir: a decisão `D-NN` citada, o achado citado,
   a etapa de `docs/01_PLANO.md`

**Não reler sem motivo:** `recovery/` (cópias forenses de 20/09),
`output/PROMPT_*`, `output/PASSO_A_PASSO_POWER_AUTOMATE.md`,
`output/GUIA_MANUAL_TRANSFERENCIA.md`, `output/PLANO_TRANSFERENCIA.*`,
`output/MAPA_RENOMEACAO.md`, `output/guia_manual.json` — são da rota Power
Automate, encerrada em 30/09 (`D-05`). Relatórios `output/*.md` de E1–E4 só
quando a tarefa tratar daquele tema.

---

## 3. Reconciliação — o que conferir contra o `STATE.md`

| Pergunta | Evidência |
|---|---|
| O HEAD citado no `STATE.md` é o atual? | `git log -1 --format=%h` |
| Houve commit depois da última atualização do estado? | `git log --format='%h %ad %s' --date=short` × data do `STATE.md` |
| Há alteração local não registrada? | `git status --short` |
| A cota citada ainda vale? | GET da cota (seção 4) — só se a tarefa depender dela |
| O catálogo é de quando? | `MAX(visto_em)` de `item_nuvem` |
| Alguma cópia ou script ficou rodando? | `tasklist //FI "IMAGENAME eq rclone.exe"` |
| Pendência marcada como aberta já foi resolvida? | log, commit ou arquivo que a resolveria |

Divergência encontrada: **mostrar**, dizer qual fonte se usou e por quê.
Nunca corrigir em silêncio durante a abertura.

---

## 4. Verificações disponíveis

Suíte: `python -m pytest -q tests` (137 testes em 11/11/2026, só local, WebDAV falso). Sem lint, typecheck,
build nem CI. Outras verificações:

| Verificação | Comando | Efeito |
|---|---|---|
| Acesso ao SharePoint | `python scripts/10_testar_graph.py` | só GET, ~30 s |
| Cota atual | GET `/sites/{id}/drives` → `quota` (via `graph_util.Graph`) | só GET |
| Arquivos vivos | GET `/drives/{id}/root` → `size` | só GET |
| Lista contra a nuvem | `bash scripts/rc.sh size prog: --files-from <lista>` | só leitura |
| Sintaxe de script Python alterado | `python -m py_compile scripts/<arquivo>.py` | local |
| Sintaxe de script bash alterado | `bash -n scripts/<arquivo>.sh` | local |

**Nunca rodar na abertura:** `210_mover_lixeira.py`, `220_podar_versoes.py`
(exceto `--simular`), `rclone copy`, `watchdog_copia.sh`, `200_transferir.ps1`.
São os scripts que escrevem ou apagam.

`config/projeto.yaml` está com `fase: analise`. Os scripts de análise chamam
`exigir_fase_analise()` e **abortam** se a fase mudar (pendência P-5).

Ao registrar validação, sempre: comando exato, resultado real, data.
Resultado de sessão anterior é "último registrado", nunca "executado agora".

---

## 5. Numeração de sessões

`SNN - Nome curto`, sequencial, em `docs/04_SESSOES.md`. O próximo número é o
maior `SNN` do arquivo + 1. S01–S03 foram reconstruídas em 04/10 a partir do
`STATE.md` e do `git log`, agrupando por bloco de trabalho — as fronteiras
exatas das conversas daquela época não são recuperáveis.

---

## 6. Segurança na continuidade

- Nunca transcrever valor de `.env`, token ou `refresh_token` em estado,
  sessão, decisão ou relatório. Nomear a variável, nunca o valor.
- O `rclone.conf` (fora do repositório) guarda o token do remote de backup.
- Antes de qualquer commit: nenhum arquivo > 10 MB sem decisão, e varrer o
  segredo do app nos arquivos rastreados (procedimento na skill `versionar`).

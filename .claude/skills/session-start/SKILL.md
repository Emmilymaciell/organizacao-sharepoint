---
name: session-start
description: Abre uma sessão neste projeto reconstruindo o estado real pelo repositório (STATE.md, Git, disco e checkpoints), reconcilia com a realidade, apresenta o briefing e PARA sem alterar nada. Invocar com /session-start ao abrir uma sessão nova.
disable-model-invocation: true
allowed-tools: Bash(git status *) Bash(git log *) Bash(git rev-parse *)
---

# /session-start

**Antes de desenvolver, reconstrua o estado real.** A conversa anterior não
existe para você: tudo vem do repositório. Esta skill reconstrói, reconcilia,
reporta e **para**.

## Proibido nesta skill

Editar, criar ou apagar arquivos · implementar ou corrigir qualquer coisa ·
atualizar documentação · instalar dependências · commit, push, stash, reset ·
rodar script que escreve (`210_mover_lixeira.py`, `220_podar_versoes.py`,
`rclone copy`, `watchdog_copia.sh`, `200_transferir.ps1`) · qualquer escrita no
SharePoint ou no Google Drive. Só leitura. Inconsistência se **relata**, não
se corrige.

## Git no momento da invocação

- Raiz: !`git rev-parse --show-toplevel`
- HEAD: !`git log -1 --format="%H | %h | %ad | %s" --date=short`

```!
git status --short --branch
```

```!
git log -10 --format="%h %ad %s" --date=short
```

## 1. Confirmar o projeto

A raiz tem que terminar em `PROG_Organizacao_SharePoint`. Se não terminar,
diga em que diretório está e pare.

## 2. Carregar, nesta ordem

1. `CLAUDE.md` — já está no contexto; não releia.
2. `STATE.md` — inteiro (é curto por desenho).
3. `docs/04_SESSOES.md` — **só a entrada do topo**.
4. `docs/00_CONTINUIDADE.md` — §1 (onde cada coisa mora e checkpoints),
   §3 (reconciliação) e §4 (verificações).

Só depois, e só se a próxima tarefa pedir: a decisão `D-NN` em
`docs/02_DECISOES.md`, o achado em `docs/03_ACHADOS.md`, a etapa em
`docs/01_PLANO.md`. Não leia `output/`, `recovery/` nem scripts sem motivo.

## 3. Reconciliar

Aplique a tabela de `docs/00_CONTINUIDADE.md` §3. No mínimo:

- HEAD citado no `STATE.md` × HEAD real; commits posteriores à data dele.
- `git status`: cada arquivo alterado ou não rastreado corresponde a algo
  registrado? Se não, é trabalho não documentado — liste-o, não mexa.
- Processo esquecido: `tasklist //FI "IMAGENAME eq rclone.exe"`.
- Data do catálogo: `MAX(visto_em)` de `item_nuvem` em
  `data/organizacao.db` (abrir com `mode=ro`). Banco ausente = clone novo;
  registre a limitação.
- Checkpoints do §1: o que eles dizem bate com a tabela "Estado de
  processamento" do `STATE.md`?
- Cada pendência aberta: há evidência de que já foi resolvida?

Classifique cada item em: concluído (com evidência) · parcial · em andamento
· não iniciado. **Evidência verificável prevalece sobre texto.** Divergência
vai para "Inconsistências", dizendo qual fonte você adotou e por quê.

Preserve as distinções do projeto: hipótese não vira fato, cenário não vira
autorização, intenção não vira trabalho feito, validação antiga não vira
validação de hoje.

## 4. Linha de base

Só se a próxima tarefa depender do SharePoint, rode as verificações de
leitura de `docs/00_CONTINUIDADE.md` §4 (teste de acesso e/ou GET da cota).
No relatório, separe **último resultado registrado** (do `STATE.md`) de
**verificado agora** (o que você rodou, com comando e resultado). Se não rodou
nada, escreva "nenhuma verificação executada nesta abertura".

## 5. Próxima unidade de trabalho

Parta de `STATE.md › Retomada`. Confirme que ainda faz sentido diante do que
a reconciliação mostrou. Se não fizer, diga por quê e proponha a menor
unidade coerente — sem replanejar o projeto. Pergunte à Emmily só o que o
repositório não responde ou o que for decisão dela (as marcadas "depende de"
na tabela de pendências).

## 6. Identificar a sessão

Número = maior `SNN` de `docs/04_SESSOES.md` + 1. Sugira um nome curto a
partir da próxima tarefa e o comando `/rename SNN - Nome`, se o ambiente o
oferecer. Ambíguo? Diga, não invente.

## 7. Briefing

Campos que não se aplicarem: omita. Curto e verificável.

```
PROJETO        Programa — Organização do SharePoint · <raiz>
SESSÃO         SNN - <nome sugerido> · objetivo: <uma linha>
GIT            <branch> · HEAD <hash curto> (<data>) · <limpo | n alterados>

ÚLTIMO MARCO VERIFICÁVEL   <o quê, quando, evidência>
CONCLUÍDO                  <com evidência>
PARCIAL / EM ANDAMENTO     <…>
PENDENTE                   <P-/T- relevantes; quem decide>
ESTADO DE PROCESSAMENTO    <frente · feito · pendente · checkpoint>

PRÓXIMA TAREFA EXATA       <ação executável, comando se houver>
CRITÉRIOS DE CONCLUSÃO     <…>
ARQUIVOS DE ALTO SINAL     <…>
DECISÕES QUE RESTRINGEM    <D-NN · uma linha cada>
SKILLS / SUBAGENTES        <só se trouxerem ganho real>

VALIDAÇÃO
  Último registrado: <data · comando · resultado>
  Verificado agora:  <comando · resultado | nenhuma>

BLOQUEIOS / RISCOS         <…>
INCONSISTÊNCIAS            <fonte A × fonte B · qual adotei>
```

Skills úteis aqui: `versionar` (commit, só a pedido), `os:handoff` (passar o
projeto a outra pessoa ou máquina), `/session-close` (encerrar). Subagente
`Explore` só para varredura ampla do código.

Termine com exatamente:

**Contexto reconstruído. Nenhuma alteração foi realizada.**

E pare. Não comece a próxima tarefa sem autorização explícita da Emmily.

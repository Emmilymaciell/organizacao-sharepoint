---
name: session-close
description: Encerra uma sessão neste projeto - para o desenvolvimento, reconcilia com Git e disco, valida o que foi feito, persiste estado, decisões, achados e o registro da sessão no repositório, e PARA. Invocar com /session-close; /session-close commit também salva uma versão.
disable-model-invocation: true
argument-hint: "[commit]"
allowed-tools: Bash(git status *) Bash(git log *) Bash(git rev-parse *)
---

# /session-close

**Se esta conversa desaparecer agora, nenhum conhecimento importante pode
desaparecer junto.** Pare o desenvolvimento, persista o estado real no
repositório, reporte e **pare**.

Argumento recebido: `$ARGUMENTS`

## Proibido nesta skill

Começar a próxima tarefa · nova funcionalidade · refatoração ou correção
oportunista · aumentar escopo · descartar, resetar, esconder (stash) ou apagar
trabalho · rodar script que escreve no SharePoint ou no Google Drive ·
declarar teste não executado · registrar intenção como resultado ou hipótese
como fato. Alterações locais que não são desta sessão: **preserve e relate**.

## Git no momento da invocação

- Raiz: !`git rev-parse --show-toplevel`
- HEAD: !`git log -1 --format="%H | %h | %ad | %s" --date=short`

```!
git status --short --branch
```

## 1. Reconciliar

1. Leia o `STATE.md` atual e a entrada do topo de `docs/04_SESSOES.md`.
2. Commits desta sessão: `git log <HEAD citado no STATE.md>..HEAD --oneline`.
3. `git diff --stat` e a lista de não rastreados: separe **o que esta sessão
   fez** do que já estava lá.
4. Processo rodando? `tasklist //FI "IMAGENAME eq rclone.exe"`. Se houver
   cópia em andamento, registre-a como "em andamento" com o log e o vigia;
   não a interrompa.

## 2. Auditar a sessão

Pergunta obrigatória: **existe informação importante que só está nesta
conversa?** Percorra a sessão inteira e classifique: concluído · parcial ·
investigado sem implementar · decisões · achados · hipóteses (e descartadas)
· bugs · riscos · pendências · bloqueios · perguntas abertas · arquivos
criados e alterados · alterações não commitadas.

Inclua o que costuma se perder: autorizações dadas por escrito (com a frase
da Emmily), números medidos, comandos que funcionaram, rotas que falharam e
por quê, e qualquer coisa apagada no SharePoint (com o log que a restaura).

## 3. Validar o que foi feito

Só o que corresponde ao trabalho real da sessão (`docs/00_CONTINUIDADE.md` §4):

| Mudou | Verificação |
|---|---|
| `scripts/*.py` | `python -m py_compile <arquivo>` |
| `scripts/*.sh` | `bash -n <arquivo>` |
| acesso ou lógica de Graph | `python scripts/10_testar_graph.py` |
| algo no SharePoint | GET da cota e do item afetado |
| só documentação | conferir links e caminhos citados |

Registre **comando exato e resultado real**. O que não puder rodar: diga que
não rodou, por quê, e o risco que fica. Nunca escreva "passou" sem execução.

## 4. Persistir — cada coisa no seu lugar

Mapa em `docs/00_CONTINUIDADE.md` §1. Não duplique: cite pelo identificador.

1. **`docs/02_DECISOES.md`** — só decisão durável de verdade, nova `D-NN` com
   contexto, decisão, motivo, consequência e fonte. Revogação vira linha
   "Revogada em", nunca apagamento.
2. **`docs/03_ACHADOS.md`** — descoberta técnica ou de domínio que continua
   valendo, com a data da medição.
3. **`docs/04_SESSOES.md`** — entrada nova **no topo**:

   ```
   ## SNN - <nome> · <data>
   - **Objetivo:** …
   - **Feito:** …            (com evidência: commit, log, contagem)
   - **Decisões / achados:** D-NN · achados §…
   - **Validado:** <comando> → <resultado>   | não validado: <motivo>
   - **Pendente:** …
   - **HEAD final:** <hash>   - **Próximo passo:** ver STATE.md › Retomada
   ```

   Número = maior `SNN` + 1, ou o que o `/session-start` sugeriu.
4. **`STATE.md`** — reescreva como **snapshot do presente**: situação,
   último marco comprovado, em andamento, estado de processamento,
   pendências, riscos, invariantes, última validação, HEAD, Retomada.
   Remova o que ficou obsoleto; histórico não fica aqui.
5. **Processamento em lote** (inventário, cópia, exclusão, poda): total,
   feito, pendente, erros, último lote e o arquivo de checkpoint, para a
   próxima sessão retomar sem recomeçar nem pular nada.

Nunca transcreva valor de `.env`, token ou senha. Nomeie a variável.

## 5. Próxima sessão

Em `STATE.md › Retomada`, **uma** ação executável — nada de "continuar a
análise". Dê o comando quando houver, critério de pronto, dependências,
riscos, o que ler e o que não precisa reler. Se ela depende de decisão da
Emmily, diga qual decisão.

## 6. Git

Política do projeto: commit **só a pedido explícito** (skill `versionar`).

- Se `$ARGUMENTS` pede commit (`commit`, `commita`, `salva versão`) **ou** a
  Emmily pediu commit nesta sessão: siga a skill `versionar`.
- Caso contrário: **não commite**. Ofereça em uma linha no relatório.
- Nunca push. Nunca `reset`, `checkout --`, `clean`, `stash`, `--amend`.

Ao final, reconfirme `git status --short --branch` e o HEAD.

## 7. Relatório

Omita o que não se aplicar.

```
SESSÃO          SNN - <nome> · <objetivo>
REPOSITÓRIO     <raiz> · <branch> · HEAD <hash> · <limpo | n alterados>

CONCLUÍDO                    <com evidência>
PARCIAL                      <…>
INVESTIGADO, NÃO FEITO       <…>
DESCOBERTAS                  <…>
DECISÕES DURÁVEIS            <D-NN>
HIPÓTESES / EM ABERTO        <…>
VALIDAÇÕES                   <comando → resultado>
NÃO COMMITADO                <arquivos>
DOCUMENTAÇÃO ATUALIZADA      <arquivos>
ESTADO DE PROCESSAMENTO      <…>
INCONSISTÊNCIAS              <…>

PRÓXIMO PASSO EXATO          <ação>
LER NA PRÓXIMA SESSÃO        <arquivos>
RISCOS / BLOQUEIOS           <…>
GIT FINAL                    <branch · HEAD · status>
/rename sugerido             <se aplicável>
```

Termine com exatamente:

**Handoff concluído. Nenhuma nova implementação foi iniciada.**

E pare.

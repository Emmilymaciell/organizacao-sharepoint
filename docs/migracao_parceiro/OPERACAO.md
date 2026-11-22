# Migração PARCEIRO — manual de operação (execução prolongada, sem o Claude)

Todos os comandos rodam num PowerShell aberto na pasta do projeto:

```powershell
cd "C:\caminho\exemplo\organizacao-sharepoint"
```

| Para | Comando |
|---|---|
| Iniciar um lote aprovado | `.\scripts\migra.ps1 iniciar <LOTE>` |
| Iniciar uma fila de lotes (em ordem) | `.\scripts\migra.ps1 iniciar "LOTE-A,LOTE-B,LOTE-C"`: antes de cada lote roda o `340` (os 10 requisitos); se um falhar, esse lote não roda e a fila segue |
| Ver o andamento | `.\scripts\migra.ps1 status` |
| Pausar (termina o que está em voo) | `.\scripts\migra.ps1 pausar` |
| Retomar de onde parou | `.\scripts\migra.ps1 retomar <LOTE>` |
| Parar com segurança e esperar | `.\scripts\migra.ps1 parar` |
| Ver o que precisa de revisão | `.\scripts\migra.ps1 falhas` |
| Gerar relatórios | `.\scripts\migra.ps1 relatorio` |
| Retomar sozinho ao entrar no Windows | `.\scripts\migra.ps1 agendar <LOTE>` · desfazer: `desagendar` |

O `iniciar` abre o runner (`339_migra_rodar_lote.ps1`) num processo próprio e
minimizado. O Claude pode ser fechado. O runner:
1. transfere (`336`);
2. se houver falha recuperável, espera 2 min e tenta de novo, até 3 rodadas;
3. confere 100% do lote (`337`);
4. gera o status (`338`).

Tudo fica registrado em `logs\migra_<lote>_<data>.log`.

## O que roda sozinho e o que para

| Situação | O motor faz |
|---|---|
| Queda de rede, timeout, 429, erro 5xx | tenta de novo o mesmo arquivo com espera crescente (5, 10, 20 s). Com 6 falhas seguidas, para de despachar e espera a rede, de 30 s a 15 min, sem queimar a fila |
| Processo ou computador interrompido | `retomar`: arquivos que estavam EM_TRANSFERENCIA voltam para a fila. Se o arquivo já chegou inteiro, é conferido por hash e **não é reenviado** |
| Paralelismo | adaptativo de 2 a 12: sobe enquanto a vazão melhora e cai pela metade com erros ou memória acima de 1,5 GB |
| Disco | nenhum arquivo novo começa se o C: ficar abaixo de 40 GiB livres |
| Suspensão do Windows | evitada **só enquanto o lote roda** (`SetThreadExecutionState`). O plano de energia **não é alterado**. Fechar a tampa ainda suspende |
| Arquivo já existe no PARCEIRO com outro conteúdo | **CONFLITO**: nada é sobrescrito |
| Permissão negada, nome inválido, cota cheia | **EXCECAO**: o arquivo é isolado e o lote segue |
| Origem mudou desde o inventário | **ORIGEM_ALTERADA**: não copia. Reinventariar |
| PARCEIRO recusa a senha (401) | **para o lote inteiro**, sem marcar arquivos como falha. Renovar com `rcparceiro.ps1 -LoginNavegador` |

| Falha transitória que esgota as tentativas (8) | **ESGOTADO**: aparece em `falhas` para revisão |
| Cota do PARCEIRO cheia (507) ou token do SharePoint expirado | **para o lote inteiro**, igual ao 401 |
| Rodar duas vezes ao mesmo tempo | a segunda execução é **recusada**. A trava é renovada a cada 60 s enquanto a primeira vive |

Códigos de saída do `336`:

| Código | Significado | O que o runner faz |
|---|---|---|
| 0 | ok | segue |
| 1 | bloqueado: fase, aprovação ou execução ativa | para |
| 2 | falhas ou pausa | nova rodada |
| 3 | parada global (credencial ou cota) | para |
| 5 | erro inesperado | nova rodada |

Os problemas ficam em `output\migracao_parceiro\excecoes.csv`, com o último motivo de
cada um. Os arquivos validados ficam em `validados.csv`, com o caminho no PARCEIRO, o
QuickXor e o SHA-256.

## Watchdog (supervisor automático)

A tarefa **"Programa Migracao PARCEIRO - Watchdog"** roda `scripts/345_migra_watchdog.py` a
**cada 10 minutos**, até 20/10/2026 às 18h. Usa a conta da Emmily e uma
instância por vez. Em cada checagem, ele:
1. coleta as evidências: processos, batimento do motor no banco, log da fila,
   progresso, PAUSAR, disco, itens retentáveis e reinícios anteriores;
2. decide com `migra_parceiro/watchdog.py`, que tem 13 cenários testados;
3. age só nos casos seguros, conforme a tabela abaixo.

| Situação | Ação |
|---|---|
| Motor batendo (≤ 15 min), mesmo lento ou num arquivo grande | nenhuma |
| Uma evidência isolada ruim | nenhuma (só registra) |
| Batimento, log **e** progresso parados além dos limites | encerra só a árvore do runner e reinicia pela tarefa |
| Runner morto com itens pendentes | reinicia pela tarefa "Programa Migracao PARCEIRO" (IgnoreNew: nunca duplica) |
| 401, cota, disco abaixo da reserva, PAUSAR, 4 reinícios em 24 h | **não reinicia**: registra o impedimento para uma pessoa |

Registros:
- `logs/watchdog.log`: uma linha por checagem;
- `logs/incidentes_migracao.csv`: tudo o que não foi SAUDÁVEL;
- `output/migracao_parceiro/RELATORIO_RETORNO.md`: resumo para quem volta, regenerado a cada checagem.

## Agendador de Tarefas e credencial

A senha do PARCEIRO está cifrada com **DPAPI da conta Windows da Emmily**. Só essa
conta, logada, consegue decifrá-la. Por isso:
- a tarefa (`migra.ps1 agendar`) roda **como a Emmily**, `LogonType Interactive`
  ("somente quando o usuário estiver conectado"), **nunca como SYSTEM**;
- o gatilho é ao entrar no Windows. Se o computador reiniciar durante a noite,
  o lote volta ao fazer login;
- deixar o computador **logado e com a tela bloqueada** (Win+L) mantém tudo
  funcionando.

## Antes de deixar rodando à noite

1. `.\scripts\migra.ps1 status`: confirme o lote aprovado e nenhum runner ativo.
2. Notebook: ligado na tomada e com a tampa aberta.
3. `.\scripts\migra.ps1 iniciar <LOTE>` e bloqueie a tela (Win+L).
4. De manhã: `.\scripts\migra.ps1 status` e `.\scripts\migra.ps1 falhas`.

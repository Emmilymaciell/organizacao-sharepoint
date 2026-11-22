# Decisões técnicas (resumo público)

> Versão pública: o registro original de decisões (D-01…D-16, com datas, autorizações e frases literais da
> operação real) foi substituído por este resumo genérico. Ficam só as decisões de engenharia.

| # | Tema | Decisão | Por quê |
|---|---|---|---|
| D-01 | Corte de revisões | Sai o que está abaixo da última revisão **aprovada** (fonte: planilha de status), não da última que existe; uma disciplina nunca é esvaziada (a maior REV fica) | revisões acima da aprovada são trabalho em tramitação |
| D-02 | Fases do projeto | O projeto nasce só leitura; escrita exige autorização explícita e a trava `fase` no config (`analise` / `execucao` / `diagnostico`) aborta scripts fora da fase | evitar escrita acidental em acervo de produção |
| D-03 | Cópia antes de apagar | Nada de uma área crítica sai sem cópia conferida, mesmo que haja duplicata idêntica em outro lugar | duplicata viva pode ser apagada depois por outra pessoa |
| D-05 | Ferramenta de transferência | rclone nuvem-a-nuvem; remote definido por variáveis de ambiente/memória, nunca em `rclone.conf`; segredo só no `.env` (fora do Git) | Power Automate falha entre tenants; Explorer/cache local não comporta arquivos de ~90 GB |
| D-06 | Histórico de versões | Manter as 2 versões mais recentes de cada arquivo; nenhum arquivo é apagado, só histórico | espaço; versão podada não vai para a lixeira, por isso a regra é conservadora |
| D-07/D-08 | Conferência de cópia | Entre provedores sem hash comum: quantidade + bytes. Na migração principal, o hash é recalculado sobre os bytes lidos do **destino** (QuickXor comparado, SHA-256 registrado) | tamanho igual não prova conteúdo igual |
| D-08 | Migração para o repositório do parceiro (WebDAV/Nextcloud) | Frente isolada (config, banco SQLite e scripts `33x` próprios); nome e caminho finais resolvidos **antes** do envio, arquivo a arquivo; motor Python em streaming como contingência | o destino não permite renomear depois; o disco local não comporta o volume |
| D-09 | Credencial do destino | Senha de aplicativo digitada em entrada oculta e cifrada com DPAPI no perfil do usuário; remote montado só em memória | nenhum segredo em disco legível nem no repositório |
| D-10 | Arquivo gigante sem conferência integral | Estado explícito `MONTADO_SEM_VALIDACAO_INTEGRAL` no banco e nos relatórios; conferência posterior possível sem reenvio | nunca declarar validado o que não foi |
| D-11/D-12 | Migração por rodovia | Uma rodovia por vez, com piloto obrigatório; escrita só condicional (`PUT If-None-Match: *`, `MOVE Overwrite: F`, `DELETE If-Match`); governador de recursos com histerese e no máximo 2 workers em prioridade baixa | não sobrescrever trabalho de terceiros; ceder recursos a processos prioritários |
| D-13 | Exclusões e movimentações | Só por autorização pontual, limitada ao objeto; pasta só é excluída após releitura recursiva vazia (de baixo para cima) | o servidor não aceita DELETE condicional em pasta |
| D-14/D-15 | Correções estruturais | Só o que é inequívoco e não destrutivo (MOVE); casos inconclusivos viram pendência para decisão humana; premissas ("é só uma cópia") conferidas por hash antes de agir | evitar perda por suposição |
| D-16 | Pacotes ZIP para o GED | Um ZIP por pasta, abaixo do limite de 100 MB, partes independentes; montado em `%TEMP%`, testado (CRC + SHA-256 por membro), copiado como `.part` e renomeado | o GED rejeita arquivos grandes; renomeação atômica evita pacote parcial |

Decisões operacionais específicas do contrato (autorizações nominais, destinos de backup, respostas a órgãos de
controle) não fazem parte da versão pública.

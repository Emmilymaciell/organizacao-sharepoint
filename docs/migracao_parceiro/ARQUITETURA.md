# Migração "5. Compartilhado PARCEIRO" → PARCEIRO INFRA — arquitetura

Frente aberta em 19/10/2026 (S05), independente do diagnóstico global do
projeto. Diagnóstico que sustenta este documento: `DIAGNOSTICO_F1.md` *(não incluído na versão pública)*.

## Isolamento

| Peça | Onde | Observação |
|---|---|---|
| Config e trava de fase própria | `config/migracao_parceiro.yaml` | `fase: diagnostico` → `piloto` → `execucao` |
| Banco de estado | `data/migracao_parceiro.db` (fora do Git) | esquema `sql/03_migracao_parceiro.sql`; não toca `organizacao.db` |
| Código | `scripts/migra_parceiro/` + `scripts/33x_migra_*.py` | reaproveita `graph_util`, `config_util` e o remote `prog:` de `rc.sh` |
| Saídas | `output/migracao_parceiro/` (fora do Git: nomes de documentos de terceiros) | CSV `;` + BOM, padrão do projeto |
| Testes | `tests/test_migra_*.py` | dados fictícios, sem rede |

| Script | Faz | Escreve fora da máquina? |
|---|---|---|
| `330_migra_eap.py` | lê a EAP, grava a árvore e cruza com o PARCEIRO | não |
| `331_migra_inventario_origem.py` | lista a pasta 5 pelo Graph (GET), retomável | não |
| `332_migra_inventario_destino.py` | lista `I:\` (scandir) | não |
| `333_migra_planejar.py` | matriz origem → destino, pastas a criar, decisões, conflitos, 272 movidos fora do escopo | não |
| `334_migra_evidencias.py` | lê ofícios e manifestações na memória (Graph) para confirmar data, referência, obra e entidade | não |
| `335_migra_lote.py` | propõe e lista lotes; **aprova só com a citação da autorização** | não |
| `336_migra_transferir.py` | executa um lote APROVADO (fase `piloto`/`execucao`) | **sim**: copia para o PARCEIRO, com autorização escrita |
| `337_migra_conferir.py` | relê 100% do lote no PARCEIRO: QuickXor + SHA-256 | não (só lê) |
| `338_migra_status.py` | painel por quantidade e bytes (`status.json`) | não |
| `rcparceiro.ps1` | único ponto que toca credencial; monta os remotes **em memória** e bloqueia comandos destrutivos | depende do comando; travado por fase |

### Credencial do PARCEIRO (D-09)

- **Remote `parceiro-infra-migracao`:**
  - definição: `webdav`, `vendor=nextcloud`, URL
    `https://nextcloud.example.com/remote.php/dav/files/usuario/`;
  - montagem: só no ambiente do processo, a cada execução de `rcparceiro.ps1`;
  - **nenhum `rclone.conf`** é lido ou gravado (`--config NUL`), e o
    `gdrive:` e os demais remotes de outros projetos ficam intocados.
- **Senha de aplicativo:**
  - entra por digitação oculta (`rcparceiro.ps1 -DefinirSenha`);
  - fica cifrada com **DPAPI** (chave da conta Windows da Emmily) em
    `%APPDATA%\prog-migracao-parceiro\`, pasta com ACL só do usuário e do SYSTEM;
  - é decifrada só na memória, passada ao rclone por variável de ambiente do
    processo filho e apagada ao sair.
  - **Limite:** qualquer processo rodando como a Emmily consegue decifrar,
    o mesmo nível do `credencial.xml` do app institucional. Revogável a
    qualquer momento em Configurações → Segurança do Nextcloud.
- **Origem:** o `prog:` é montado pelo mesmo wrapper, com as credenciais do `.env`, igual ao `rc.sh`.

### Conectividade validada (19/10/2026)

- **Credencial:** senha de aplicativo obtida pelo **Login Flow v2**
  (`rcparceiro.ps1 -LoginNavegador`). A Emmily aprovou no navegador, e o servidor
  devolveu o login `usuario` e a senha direto para o DPAPI.
- **Duas armadilhas corrigidas.** Ambas foram diagnosticadas com um WebDAV
  falso em 127.0.0.1, sem nenhuma tentativa extra no PARCEIRO:
  1. o rclone 1.75 **não acha remote definido por variável de ambiente se o
     nome tiver hífen**. O nome interno virou `parceiro_infra_migracao`, e o
     wrapper traduz `parceiro-infra-migracao:`;
  2. no `powershell -NoProfile`, o pipe para o `rclone obscure` **prefixava
     um BOM (U+FEFF)**, e o servidor recebia 73 caracteres para uma senha de
     72, o que dava 401. A codificação do console agora é fixada sem
     preâmbulo.
- **Estrutura:** `lsd parceiro-infra-migracao:` mostra as 7 pastas institucionais
  e `PARCEIRO-INFRA`. A raiz e `PARCEIRO-INFRA/` têm o **mesmo conteúdo**: em `02.
  Programa`, 15 pastas, 36 arquivos e 251.682.536 B nas duas. O destino da
  migração é `parceiro_infra_migracao:PARCEIRO-INFRA`, igual a `I:\`.
- **Permissões** (`rcparceiro.ps1 -Permissoes`, PROPFIND): `PARCEIRO-INFRA` é um
  armazenamento externo montado na conta da Emmily. As pastas-alvo têm
  `RMGDNVCK` (criar pasta e arquivo, renomear, mover, apagar). O motor não usa
  renomear nem apagar.

### Motor (`migra_parceiro/motor.py` + `rc.py`)

- **Execução:**
  - um `rclone rcd` (API em 127.0.0.1, usuário e senha aleatórios por
    execução, `--rc-serve`) por lote;
  - cada arquivo é um `operations/copyfile` com o caminho de origem e o
    **caminho final**, com `Immutable=true`;
  - paralelismo configurável;
  - fila no banco, retentativas (até `max_tentativas`) e trava de execução
    única;
  - pausa por arquivo `tmp/migracao_parceiro/PAUSAR`;
  - parada automática por disco.
- **Travas de segurança:**
  - o cliente `rc.py` só chama `stat`, `copyfile`, `noop`, `stats` e `quit`:
    não existe caminho de código para apagar, mover ou sincronizar;
  - **destino já ocupado:** o arquivo é relido e comparado por hash. Igual
    vira `JA_EXISTIA` e não é reenviado; diferente vira `CONFLITO` e nada é
    sobrescrito;
  - **origem alterada** desde o inventário: o arquivo não é copiado e vira
    `ORIGEM_ALTERADA`.
- **Conferência:** os bytes são lidos do PARCEIRO pelo rcd, sem cache de disco.
  O QuickXor (numpy, cerca de 1 GB/s, validado contra o rclone) e o SHA-256
  saem da mesma leitura. Os 52 arquivos sem hash do servidor têm a origem
  relida e comparada por SHA-256.
- **Testes:** `tests/test_migra_motor.py` roda o rclone real entre duas pastas
  locais e cobre:
  - renomeação no envio e não sobrescrita;
  - arquivo idêntico já existente e conflito;
  - divergência de conteúdo;
  - retomada, pausa, disco e trava;
  - bloqueio dos métodos destrutivos;
  - origem intocada byte a byte.

## Por onde passam os bytes — comparação dos métodos

Nenhum dos quatro métodos faz cópia **servidor-a-servidor**. SharePoint
(Microsoft) e Nextcloud (PARCEIRO) são provedores diferentes; o rclone informa
`ServerSideAcrossConfigs: false`. Todo byte desce do SharePoint e sobe ao
PARCEIRO **passando por este computador**. A diferença entre os métodos é se os
bytes passam pela **memória** ou pelo **disco**.

| | A · Graph → WebDAV (motor próprio) | B · rclone `prog:` → `parceiro:` (WebDAV) | C · pasta sincronizada → `I:\` | D · motor Python com streaming |
|---|---|---|---|---|
| Disco local por arquivo | ~0 (buffer em RAM) | ~0 (buffer em RAM; a confirmar no piloto) | **2× o arquivo**: hidratação do OneDrive + cache VFS da montagem | ~0 |
| Renomear no caminho | sim | sim (`copyto` / `operations/copyfile` por arquivo) | sim, mas pelo Explorer/robocopy | sim |
| Concorrência | ajustável | ajustável (`--transfers`) | **fixa em 2** (`--transfers 2` do app) | ajustável |
| Caminhos > 260 car. | n/a | n/a | **9.950 arquivos** acima de 260 | n/a |
| Arquivo de 87,76 GiB | upload em blocos | upload em blocos (chunking Nextcloud) | precisa de ~176 GiB livres no C: | upload em blocos |
| Retomada / retentativa | a implementar | nativas no rclone + fila no banco | não | a implementar |
| Hash na transferência | SHA-256 + QuickXor da origem, de graça | não (sem hash em comum) | não | SHA-256 + QuickXor da origem, de graça |
| Credencial do PARCEIRO | usuário + senha de app | usuário + senha de app | nenhuma nova | usuário + senha de app |
| Esforço | alto | baixo | baixo | médio |

**C está descartado.** Cada arquivo seria baixado inteiro para o C: pelo
OneDrive e gravado de novo inteiro no cache de `I:\` (`--vfs-cache-mode writes`,
teto de 10 GiB que o próprio rclone excede para arquivo aberto). O vídeo de
87,76 GiB não cabe nos 78 GiB livres. Além disso, a concorrência fica fixa em
2 e há 9.950 caminhos longos. O modo "Enviar pasta grande" do app institucional
tem o mesmo problema, porque parte de uma pasta **local**.

## Recomendação

**Principal: B, com rclone nas duas pontas e um planejador Python na frente.**

```
Planejador (333) ──► fila em migracao_parceiro.db ──► rclone rcd (operations/copyfile)
                                                      │  prog:<origem>  ──RAM──►  parceiro:<destino final>
Conferência (336) ◄── leitura independente do destino (WebDAV GET) ──► QuickXor + SHA-256
```

- **Origem:** o remote `prog:` que já existe (`scripts/rc.sh`, credenciais do `.env`,
  só leitura na prática: `copyfile` não altera a origem). Proibidos no código:
  `move`, `sync`, `--delete-*`.
- **Destino:** um remote `parceiro:` próprio deste projeto, do tipo `webdav` e
  `vendor=nextcloud`, com a URL
  `https://nextcloud.example.com/remote.php/dav/files/<usuário>/PARCEIRO-INFRA/`. É o
  mesmo canal que o app institucional usa, só que com uma **senha de aplicativo**
  criada por você e guardada no `.env`/Bitwarden. Não se lê `credencial.xml`,
  nem o ambiente do processo da montagem.
- **Renomeação no envio:** `operations/copyfile` recebe o caminho de origem e o
  caminho final de destino por arquivo. Pastas intermediárias são criadas pelo
  envio (MKCOL), **só as listadas em `pastas_a_criar.csv` e aprovadas**.
- **Sem sobrescrita:** antes de cada envio, o motor confere se o caminho final
  já existe no PARCEIRO. Se existir, o item vira `CONFLITO` e nada é enviado. O
  `--immutable` fica como segunda barreira.
- **Paralelismo:** N cópias simultâneas, com N medido no piloto (começa em 4).
  O arquivo de 87,76 GiB vai em lote próprio.
- **Verificação (Fase 8):**
  - Cada arquivo é lido de volta **do PARCEIRO**, por GET WebDAV, sem cache do
    `I:\`.
  - Na mesma leitura calculam-se o **QuickXorHash** (o mesmo algoritmo que o
    SharePoint calculou no servidor sobre os bytes da origem) e o **SHA-256**
    (impressão digital para o termo de homologação).
  - VALIDADO = QuickXor do destino igual ao QuickXor da origem e mesmo tamanho.
    Nenhuma comparação mistura algoritmos.
  - Os 52 arquivos sem hash na origem recebem leitura dupla (origem e destino)
    com SHA-256 dos dois lados.

**Contingência: D, com motor Python em streaming.** Usar se o piloto mostrar
que o rclone grava em disco, falha no chunking com permissão de compartilhamento
ou não sustenta a vazão. O motor lê a origem por streaming
(`GraphRO.baixar()` do Cérebro Documental), calcula SHA-256 e QuickXor
enquanto lê, envia em blocos pela API de chunked upload v2 do Nextcloud e
valida no fim. Ganha SHA-256 da origem sem segunda leitura; custa mais código.

**Modo estrito (opcional):** se você exigir SHA-256 calculado também sobre os
bytes da origem, B precisa ler a origem uma segunda vez (cerca de 220 GiB a
mais de download) ou se troca B por D. A decisão sai depois do piloto, com
números.

## Espaço local

- **Situação medida em 19/10:** C: com 474 GiB, 78 GiB livres. Cache da
  montagem PARCEIRO vazio.
- **Limites** (`config/migracao_parceiro.yaml`, configuráveis):
  - reserva mínima de 40 GiB livres;
  - teto de 8 GiB para temporários próprios, em `tmp/migracao_parceiro/`.
  - `migra_parceiro/espaco.py` bloqueia o próximo envio quando um desses limites
    seria violado.
- **O que a migração nunca faz:**
  - não lê nem hidrata a pasta sincronizada (usada só para metadados);
  - não muda o estado online/offline de nada;
  - não limpa cache de outro programa;
  - não escreve em `I:\`.

## Portões de aprovação

| Portão | O que libera | Quem |
|---|---|---|
| G1 | criar a senha de app no Nextcloud e o remote `parceiro:` (só leitura: PROPFIND de permissões) | Emmily |
| G2 | lote-piloto: N arquivos pequenos e de baixo risco, com destino resolvido, e criação só das pastas desse lote | Emmily, por escrito |
| G3 | execução por lote, depois das decisões de classificação | Emmily, por escrito, lote a lote |
| G4 | homologação | Emmily |
| — | **exclusão no SharePoint** | fora desta demanda; autorização separada |

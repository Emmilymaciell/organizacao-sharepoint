# Migração PARCEIRO — proposta de lote-piloto (Fase 5)

**Situação: PROPOSTO, não aprovado.** Os lotes existem só no banco local
(`335_migra_lote.py listar PILOTO-0N`). Nenhum arquivo foi enviado e nenhuma
pasta foi criada.

## Pré-requisitos, em ordem

1. **G1 — conectividade**: a Emmily cadastra a senha de aplicativo
   (`rcparceiro.ps1 -DefinirSenha`). Depois disso, só leitura:
   - `rcparceiro.ps1 lsd parceiro-infra-migracao:`
   - `rcparceiro.ps1 lsd parceiro-infra-migracao:PARCEIRO-INFRA`
   - comparação com a árvore de `I:\`
   - PROPFIND das permissões das pastas-alvo
2. **Autorização escrita** para mudar `config/migracao_parceiro.yaml` de
   `fase: diagnostico` para `fase: piloto`.
3. **Aprovação escrita de cada lote**:
   `335_migra_lote.py aprovar PILOTO-01 --texto "<citação>"`.

## Lotes

| Lote | O que testa | Arquivos | GiB | Pastas novas |
|---|---|---:|---:|---:|
| **PILOTO-01** (funcional) | `10.` anual 2025, a resposta TCE da RD-104 (pacote renomeado), `05./06. Dezembro 2025/G5` (grupo renomeado e um nome NFD→NFC) e `0. Geral/Documentos Consorciadas` (reuso da pasta existente `01.Gestão do Contrato` e período → `2026-T1`) | 45 | 0,33 | 16 |
| **PILOTO-02** (volume) | o pacote completo `5_Prestação de Contas (out a dez-25)` → `03. Estruturadora/02. Trimestral/2025-T4`: muitos arquivos pequenos, árvore profunda e caminhos longos | 904 | 2,83 | 281 |
| **PILOTO-03** (arquivos grandes, opcional) | `17_…/14. SINALIZAÇÃO…`: dois arquivos de 3,4 GiB e outros grandes, para medir o upload em blocos, a RAM e o disco | 47 | 14,24 | 14 |

As listas completas, com origem, destino e nome final, ficam em
`output/migracao_parceiro/lote_PILOTO-0N.csv` e
`lote_PILOTO-0N_pastas_a_criar.csv`.

## O que se mede e como

| Medida | Como |
|---|---|
| Vazão (atual e média), tempo, ETA | `progresso.json` do 336, a cada 5 s |
| Concorrência ideal | PILOTO-02 com 4 envios simultâneos; repetir trechos com 2 e 8 se a vazão não escalar |
| Disco | o C: livre é conferido antes de cada arquivo (reserva de 40 GiB). O rclone usa `--temp-dir` próprio (`tmp/migracao_parceiro/rclone`), medido no fim |
| RAM | `core/memstats` do rcd e o gerenciador de tarefas |
| Placeholders do OneDrive | a origem é lida pela API, não pela pasta sincronizada. Antes e depois, conferir que os bytes hidratados da pasta local continuam em ~2 MB (`os.scandir`) |
| Renomeação no envio | existência do caminho e do nome final no PARCEIRO (`stat`) |
| Criação de pastas | antes e depois de `332_migra_inventario_destino.py`: devem aparecer **somente** as pastas de `lote_*_pastas_a_criar.csv` |
| Integridade | `337_migra_conferir.py`: 100% dos arquivos relidos do PARCEIRO, QuickXor igual ao do servidor de origem e SHA-256 registrado |
| Persistência | a conferência lê os bytes do servidor via rcd, sem o cache de `I:\`. Repetir a conferência no dia seguinte |
| Retomada | interromper o PILOTO-02 no meio (arquivo `PAUSAR` ou Ctrl+C) e rodar de novo: nada é reenviado nem sobrescrito |

## Critério de sucesso do piloto

- 100% dos arquivos do lote em **VALIDADO**, ou com motivo registrado (CONFLITO ou
  ORIGEM_ALTERADA).
- Nenhuma pasta criada fora da lista aprovada e nenhum arquivo existente alterado.
- Disco local sem crescimento além do `--temp-dir` e bytes hidratados da pasta
  sincronizada inalterados.
- A retomada não reenvia nem sobrescreve nada.

## Riscos

| Risco | Mitigação |
|---|---|
| A senha de aplicativo não ter permissão de criar pasta nas pastas compartilhadas | o PILOTO-01 começa por um lote de 45 arquivos. Se falhar, nada fica pela metade e o erro aparece por arquivo |
| Upload em blocos do Nextcloud recusado na pasta compartilhada | o PILOTO-03 isola os grandes. A contingência é `--webdav-nextcloud-chunk-size 0` (PUT direto) para arquivos ≤ limite do servidor |
| Nome definitivo errado (não há como renomear depois) | os nomes vêm de `lote_*.csv`, revisados antes da aprovação |
| Nome de pacote TCE fora do padrão que a Emmily quer | o texto exato está na aprovação do PILOTO-01 |

"""
migra_parceiro - modulo da migracao "5. Compartilhado PARCEIRO" -> PARCEIRO INFRA.

Frente independente do diagnostico global: banco proprio
(data/migracao_parceiro.db), config propria (config/migracao_parceiro.yaml),
saidas em output/migracao_parceiro/. Scripts de entrada: scripts/33x_migra_*.py.

Regras do modulo (docs/migracao_parceiro/ARQUITETURA.md):
- a origem nunca e alterada: copiar, nunca mover;
- nada e escrito no PARCEIRO sem autorizacao escrita da Emmily na conversa;
- nome e caminho finais sao resolvidos antes do envio (o destino nao renomeia);
- arquivo so e VALIDADO com hash conferido sobre os bytes lidos do destino.
"""

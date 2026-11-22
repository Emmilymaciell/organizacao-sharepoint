# Sanitização desta versão pública

Cópia sanitizada de um projeto em uso real. Nada aqui é dado de cliente ou de terceiro: o que aparece como exemplo é inventado. Detalhe completo da sanitização: relatório interno da autora.

## O que foi removido

Registro de decisões operacionais (`docs/02_DECISOES.md` original: autorizações nominais, destino de backup pessoal) e o script de decisão sobre respostas a órgão de controle (`341`); nota sobre credencial trafegada por e-mail.

## O que foi trocado

`docs/02_DECISOES.md` → resumo público só com decisões de engenharia; nome do remote de backup → `backup:`; programa, parceiro, rodovias e órgãos → nomes genéricos; IDs do Graph → zerados.

## O que é fictício

Fixtures dos testes são sintéticas (árvores e bancos em Temp).

## Como verificar

```bat
py -3.12 -m pytest tests -q        # 137 testes
```

Varreduras (2026-11-19): gitleaks `dir` só com falsos positivos justificados; busca por caminho local, e-mail, nomes do cliente e links privados sem ocorrências reais.

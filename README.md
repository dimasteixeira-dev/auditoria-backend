# Backend — Plataforma de Auditoria de Carteira Solar

API real (FastAPI + SQLAlchemy) que implementa o modelo de dados e o motor de
regras das Fases 1 a 8 do projeto. **Testada de ponta a ponta contra a
planilha real da AAMN** — ver `scripts/import_and_test.py` e a seção
"Validação" abaixo.

## Rodando localmente (SQLite, zero configuração)

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Abra `http://localhost:8000/docs` para a documentação interativa (Swagger).

## Importando dados reais

```bash
curl -X POST http://localhost:8000/importar -F "arquivo=@sua_planilha.xlsx"
curl -X POST "http://localhost:8000/recalcular?competencia=2026-07-01"
curl "http://localhost:8000/dashboard?competencia=2026-07-01"
```

O endpoint `/importar` aceita o mesmo `.xlsx` que a operação já usa hoje
(abas RATEIO, GERACAO, TARIFAS, EXTRATO_DETALHADO, CHAMADOS) e nunca apaga
dado histórico — tudo é upsert por chave natural.

## Validação (Fase 9 — teste de fidelidade automatizado)

```bash
python scripts/import_and_test.py /caminho/para/auditoria.xlsx
```

Esse script importa a planilha real, calcula os indicadores da usina AAMN –
UFV 1 para 07/2026 pelo motor de regras do backend, e compara automaticamente
com os valores conferidos manualmente na planilha original. Última execução:
**todos os 8 indicadores testados bateram exatamente**, incluindo o Health
Score (69,90 nos dois lados).

Durante essa validação, dois bugs reais foram encontrados e corrigidos na
implementação (não existiam na planilha):
1. Eficiência de Rateio, Vacância, Faturamento Bruto e Inadimplência devem
   ser agregados por **nome da usina no extrato inteiro** (todas as contas
   ligadas à usina), não pela lista curada de UCs do RATEIO — é assim que a
   planilha original calcula.
2. Faturamento Bruto Realizado / MRR Realizado usam **visão de caixa**: o
   filtro é pela *data de pagamento* da fatura, não pela competência dela.
   Uma fatura de maio paga em julho conta como caixa de julho.

Ambos ficaram documentados como comentários no código (`app/rules.py`).

## Indo para produção

- **Banco de dados**: troque `DATABASE_URL` para Postgres
  (`postgresql+psycopg2://usuario:senha@host:5432/auditoria_solar`). O
  `Dockerfile` já assume Postgres por padrão.
- **Migrações**: este protótipo usa `Base.metadata.create_all` (cria tabelas
  se não existirem). Para produção, adote Alembic para versionar alterações
  de schema sem perder dado.
- **Autenticação**: não implementada aqui — adicione OAuth2/JWT antes de
  expor a API publicamente. Os campos `responsavel` já existem no modelo
  para quando houver usuários reais.
- **Agendamento**: rode `/recalcular` automaticamente após cada `/importar`
  bem-sucedido (ou num cron mensal, no fechamento de competência) para os
  alertas ficarem sempre atualizados.
- **Integração HubSpot**: os Chamados continuam manuais, fiel à planilha
  original (ela também não tinha acesso à API). Quando houver credenciais,
  o módulo de Chamados é o ponto natural para automatizar a criação/baixa.
- **CORS e frontend**: o frontend (`plataforma_completa.jsx`, entregue
  separadamente) hoje roda com dados de exemplo em memória; para conectar ao
  backend real, troque os arrays `USINAS`/`UCS`/etc. por chamadas a este
  API e configure CORS em `app/main.py`.

## Estrutura

```
app/
  database.py    — conexão (SQLite dev / Postgres produção)
  models.py      — modelo de dados (Fase 2)
  rules.py       — motor de regras (Fases 1-3), idêntico às fórmulas da planilha
  importers.py   — leitura do .xlsx e upsert no banco
  schemas.py     — contratos da API (Pydantic)
  main.py        — endpoints FastAPI
scripts/
  import_and_test.py — importação real + teste de fidelidade automatizado
```

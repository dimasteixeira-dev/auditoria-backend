"""
Uso: python scripts/import_and_test.py /caminho/para/auditoria.xlsx

1. Cria as tabelas (SQLite local, apagando qualquer banco de teste anterior).
2. Importa a planilha real (RATEIO, GERACAO, TARIFAS, EXTRATO_DETALHADO, CHAMADOS).
3. Calcula os indicadores da usina real (AAMN – UFV 1) para 07/2026 e compara
   com os valores conferidos manualmente na Fase 9 do documento de análise.
4. Sobe a API em processo (TestClient) e chama /dashboard e /alertas para
   provar que a camada HTTP também funciona de ponta a ponta.
"""
import sys
import os
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DB_PATH = "test_import.db"
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
os.environ["DATABASE_URL"] = f"sqlite:///./{DB_PATH}"

from app.database import Base, engine, SessionLocal  # noqa: E402
from app import models as m, rules, importers  # noqa: E402

Base.metadata.create_all(bind=engine)
db = SessionLocal()

path = sys.argv[1] if len(sys.argv) > 1 else "../auditoria.xlsx"
print(f"Importando {path} ...")
resultado = importers.importar_tudo(db, path)
print("Resultado da importação:", resultado)

usina = db.query(m.Usina).filter(m.Usina.nome.ilike("%AAMN%")).first()
if not usina:
    print("ERRO: usina AAMN não encontrada após importação.")
    sys.exit(1)

competencia = date(2026, 7, 1)
params = rules.get_parametros(db)
ind = rules.calcular_indicadores_usina(db, usina, competencia)
score = rules.health_score(ind, params)
status = rules.status_from_score(score, params)
diag = rules.diagnostico(ind, params)

esperado = {
    "faturamento_potencial": 4746.32,
    "mrr_realizado": 845.35,
    "eficiencia_rateio": 0.9528,
    "vacancia": -0.4364,
    "capturas_pendentes": 3,
    "saldo_acumulado_kwh": 0.0,
    "inadimplencia_mes": 0.0,
    "health_score": 69.90,
}
obtido = {
    "faturamento_potencial": round(ind.faturamento_potencial, 2),
    "mrr_realizado": round(ind.mrr_realizado, 2),
    "eficiencia_rateio": round(ind.eficiencia_rateio, 4),
    "vacancia": round(ind.vacancia, 4),
    "capturas_pendentes": ind.capturas_pendentes,
    "saldo_acumulado_kwh": ind.saldo_acumulado_kwh,
    "inadimplencia_mes": ind.inadimplencia_mes,
    "health_score": round(score, 2),
}

print("\n=== Comparação com o teste de fidelidade (Fase 9) ===")
print(f"{'Indicador':28} {'Planilha':>12} {'Backend':>12}  OK?")
tudo_ok = True
for chave in esperado:
    e, o = esperado[chave], obtido[chave]
    ok = abs(e - o) < 0.05 if isinstance(e, float) else e == o
    tudo_ok = tudo_ok and ok
    print(f"{chave:28} {e:>12} {o:>12}  {'✅' if ok else '❌'}")
print(f"Status calculado: {status}  |  Diagnóstico: {diag}")
print("\nRESULTADO GERAL:", "TODOS OS INDICADORES BATERAM ✅" if tudo_ok else "HÁ DIVERGÊNCIA — revisar ❌")

print("\nGerando alertas e testando a API em processo...")
rules.gerar_alertas(db, competencia)
db.close()

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)
r = client.get("/dashboard", params={"competencia": "2026-07-01"})
print("GET /dashboard ->", r.status_code)
d = r.json()
print(f"  total_usinas={d['total_usinas']}  usinas_atencao={d['usinas_atencao']}  usinas_criticas={d['usinas_criticas']}")

r2 = client.get("/alertas", params={"competencia": "2026-07-01"})
print("GET /alertas ->", r2.status_code, f"({len(r2.json())} alertas)")
for a in r2.json()[:5]:
    print("  -", a["prioridade"], "|", a["categoria"], "|", a["problema_identificado"])

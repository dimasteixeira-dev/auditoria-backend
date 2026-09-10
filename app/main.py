import os
import shutil
import tempfile
from datetime import date
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from . import models as m, schemas as s, rules
from .database import Base, engine, get_db
from .importers import importar_tudo

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Plataforma de Auditoria de Carteira Solar",
    description="API do painel de auditoria operacional e financeira de usinas solares.",
    version="1.0.0",
)


def _indicadores_out(db: Session, usina: m.Usina, competencia: date, params: dict) -> s.IndicadoresOut:
    ind = rules.calcular_indicadores_usina(db, usina, competencia)
    score = rules.health_score(ind, params)
    return s.IndicadoresOut(
        usina_id=usina.id, nome=usina.nome, competencia=ind.competencia, health_score=round(score, 2),
        status=rules.status_from_score(score, params), diagnostico=rules.diagnostico(ind, params),
        faturamento_potencial=ind.faturamento_potencial, faturamento_bruto_realizado=ind.faturamento_bruto_realizado,
        mrr_realizado=ind.mrr_realizado, gap_faturamento=ind.mrr_realizado - ind.faturamento_potencial,
        inadimplencia_mes=ind.inadimplencia_mes, inadimplencia_acumulada=ind.inadimplencia_acumulada,
        eficiencia_rateio=ind.eficiencia_rateio, vacancia=ind.vacancia, capturas_pendentes=ind.capturas_pendentes,
        saldo_acumulado_kwh=ind.saldo_acumulado_kwh, chamados_abertos=ind.chamados_abertos,
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/dashboard", response_model=s.DashboardOut)
def dashboard(competencia: date, db: Session = Depends(get_db)):
    params = rules.get_parametros(db)
    usinas = db.query(m.Usina).all()
    linhas = [_indicadores_out(db, u, competencia, params) for u in usinas]
    return s.DashboardOut(
        competencia=rules.primeiro_dia_mes(competencia),
        total_usinas=len(usinas),
        faturamento_potencial=sum(l.faturamento_potencial for l in linhas),
        mrr_realizado=sum(l.mrr_realizado for l in linhas),
        gap_faturamento=sum(l.gap_faturamento for l in linhas),
        inadimplencia_mes=sum(l.inadimplencia_mes for l in linhas),
        chamados_abertos=db.query(m.Chamado).filter(m.Chamado.status.in_(["Aberto", "Em andamento"])).count(),
        usinas_criticas=len([l for l in linhas if l.status == "CRITICO"]),
        usinas_atencao=len([l for l in linhas if l.status == "ATENCAO"]),
        usinas=linhas,
    )


@app.get("/usinas", response_model=list[s.UsinaOut])
def listar_usinas(db: Session = Depends(get_db)):
    return db.query(m.Usina).all()


@app.get("/usinas/{usina_id}/indicadores", response_model=s.IndicadoresOut)
def indicadores_usina(usina_id: int, competencia: date, db: Session = Depends(get_db)):
    usina = db.get(m.Usina, usina_id)
    if not usina:
        raise HTTPException(404, "Usina não encontrada")
    params = rules.get_parametros(db)
    return _indicadores_out(db, usina, competencia, params)


@app.get("/alertas", response_model=list[s.AlertaOut])
def listar_alertas(competencia: date, categoria: str | None = None, prioridade: str | None = None, db: Session = Depends(get_db)):
    q = db.query(m.Alerta).filter(m.Alerta.competencia == rules.primeiro_dia_mes(competencia))
    if categoria:
        q = q.filter(m.Alerta.categoria == categoria)
    if prioridade:
        q = q.filter(m.Alerta.prioridade == prioridade)
    ordem = {"critico": 0, "atencao": 1, "medio": 2, "info": 3}
    return sorted(q.all(), key=lambda a: ordem.get(a.prioridade, 9))


@app.get("/rotina", response_model=list[s.AlertaOut])
def minha_rotina(competencia: date, db: Session = Depends(get_db)):
    """Mesma lista de /alertas, priorizada — a tela 'Minha Rotina' consome este endpoint."""
    return listar_alertas(competencia, db=db)


@app.post("/recalcular")
def recalcular(competencia: date, db: Session = Depends(get_db)):
    """Gera/atualiza os alertas da competência a partir dos dados já importados. Idempotente."""
    alertas = rules.gerar_alertas(db, rules.primeiro_dia_mes(competencia))
    return {"alertas_gerados_ou_atualizados": len(alertas)}


@app.post("/importar")
def importar(arquivo: UploadFile = File(...), db: Session = Depends(get_db)):
    """Recebe um .xlsx no mesmo formato usado hoje e faz upsert de tarifas, usinas, UCs,
    geração, faturas e chamados. Nunca apaga dado histórico já existente."""
    if not arquivo.filename.endswith(".xlsx"):
        raise HTTPException(400, "Envie um arquivo .xlsx")
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        shutil.copyfileobj(arquivo.file, tmp)
        tmp_path = tmp.name
    try:
        resultado = importar_tudo(db, tmp_path)
    finally:
        os.unlink(tmp_path)
    return resultado

import os
import shutil
import tempfile
from datetime import date
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
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

# CORS: permite que o site (Vercel) fale com esta API (Render). Em produção,
# troque "*" pela URL exata do seu site (ex.: https://auditoria-gold.vercel.app)
# para restringir quem pode chamar a API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
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
    out = []
    for u in db.query(m.Usina).all():
        out.append(s.UsinaOut(
            id=u.id, nome=u.nome, distribuidora=u.distribuidora.nome if u.distribuidora else None,
            uf=u.uf, gd=u.gd, modalidade=u.modalidade, uc_ancora_numero=u.uc_ancora_numero,
            pct_administracao=u.pct_administracao, desconto_cliente=u.desconto_cliente,
            status=u.status, responsavel=u.responsavel,
        ))
    return out


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


@app.get("/ucs", response_model=list[s.UcOut])
def listar_ucs(db: Session = Depends(get_db)):
    ucs = db.query(m.UC).all()
    return [
        s.UcOut(
            id=uc.id, usina_id=uc.usina_id, usina_nome=uc.usina.nome, numero=uc.numero,
            apelido=uc.apelido, consumo_compensavel_kwh=uc.consumo_compensavel_kwh,
            saldo_kwh=uc.saldo_kwh, autonomia_meses=uc.autonomia_meses,
            rateio_ideal_pct=uc.rateio_ideal_pct, rateio_verificado_pct=uc.rateio_verificado_pct,
        )
        for uc in ucs
    ]


@app.get("/chamados", response_model=list[s.ChamadoOut])
def listar_chamados(status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(m.Chamado)
    if status:
        q = q.filter(m.Chamado.status == status)
    return [
        s.ChamadoOut(
            id=c.id, usina_id=c.usina_id, usina_nome=c.usina.nome, tipo=c.tipo,
            descricao=c.descricao, qtd_ucs=c.qtd_ucs, data_abertura=c.data_abertura,
            status=c.status, impacto_mrr=c.impacto_mrr,
        )
        for c in q.all()
    ]


@app.get("/tarifas", response_model=list[s.TarifaOut])
def listar_tarifas(db: Session = Depends(get_db)):
    out = []
    for dist in db.query(m.Distribuidora).all():
        atual = (
            db.query(m.TarifaHistorico)
            .filter_by(distribuidora_id=dist.id)
            .order_by(m.TarifaHistorico.vigencia_inicio.desc())
            .first()
        )
        if atual:
            out.append(s.TarifaOut(
                distribuidora=dist.nome, uf=dist.uf, tarifa_fornecida=atual.tarifa_fornecida,
                tarifa_injetada_gd1=atual.tarifa_injetada_gd1, tarifa_injetada_gd2=atual.tarifa_injetada_gd2,
                tarifa_injetada_autoconsumo_gd1=atual.tarifa_injetada_autoconsumo_gd1,
                tarifa_injetada_autoconsumo_gd2=atual.tarifa_injetada_autoconsumo_gd2,
            ))
    return out


@app.get("/rateio", response_model=list[s.RateioLinhaOut])
def rateio(competencia: date, db: Session = Depends(get_db)):
    return rules.rateio_detalhado(db, competencia)


@app.get("/geracao", response_model=list[s.GeracaoUsinaOut])
def geracao(db: Session = Depends(get_db)):
    return rules.geracao_series(db)


@app.get("/capturas-pendentes", response_model=list[s.CapturaPendenteOut])
def capturas_pendentes(competencia: date, db: Session = Depends(get_db)):
    return rules.capturas_pendentes_detalhe(db, competencia)


@app.get("/inadimplencia", response_model=list[s.InadimplenciaOut])
def inadimplencia(db: Session = Depends(get_db)):
    return rules.inadimplencia_detalhe(db)


@app.get("/usinas/{usina_id}/auditorias", response_model=list[s.AuditoriaPontoOut])
def auditorias(usina_id: int, db: Session = Depends(get_db)):
    return rules.auditorias_usina(db, usina_id)

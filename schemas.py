from datetime import date
from pydantic import BaseModel


class UsinaOut(BaseModel):
    id: int
    nome: str
    uf: str | None
    gd: int | None
    modalidade: str | None
    pct_administracao: float
    desconto_cliente: float
    status: str | None
    responsavel: str | None

    class Config:
        from_attributes = True


class IndicadoresOut(BaseModel):
    usina_id: int
    nome: str
    competencia: date
    health_score: float
    status: str
    diagnostico: str
    faturamento_potencial: float
    faturamento_bruto_realizado: float
    mrr_realizado: float
    gap_faturamento: float
    inadimplencia_mes: float
    inadimplencia_acumulada: float
    eficiencia_rateio: float
    vacancia: float
    capturas_pendentes: int
    saldo_acumulado_kwh: float
    chamados_abertos: int


class AlertaOut(BaseModel):
    id: int
    categoria: str
    prioridade: str
    usina_id: int
    uc_id: int | None
    competencia: date
    problema_identificado: str | None
    indicador: str | None
    valor_atual: str | None
    valor_esperado: str | None
    acao_recomendada: str | None
    status: str

    class Config:
        from_attributes = True


class DashboardOut(BaseModel):
    competencia: date
    total_usinas: int
    faturamento_potencial: float
    mrr_realizado: float
    gap_faturamento: float
    inadimplencia_mes: float
    chamados_abertos: int
    usinas_criticas: int
    usinas_atencao: int
    usinas: list[IndicadoresOut]

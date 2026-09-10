"""
Motor de regras da plataforma de auditoria.

Cada função aqui é a tradução direta de uma fórmula da planilha original —
a referência entre parênteses aponta para a célula/aba de origem (ver o
documento de análise, Fases 1 a 3). Nada aqui foi inventado: onde a planilha
deixava uma regra em aberto ("REGRA A VALIDAR"), a decisão tomada está
marcada com [ASSUMIDO] e documentada na Fase 1.6 / 10.
"""
from dataclasses import dataclass
from datetime import date
from calendar import monthrange
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from . import models as m


def primeiro_dia_mes(d: date) -> date:
    return date(d.year, d.month, 1)


def edate(d: date, meses: int) -> date:
    """Equivalente ao EDATE() do Excel, sempre normalizado para o dia 1."""
    total = d.year * 12 + (d.month - 1) + meses
    ano, mes = divmod(total, 12)
    return date(ano, mes + 1, 1)


def get_parametros(db: Session) -> dict:
    defaults = {
        "autonomia_maxima_meses": (3.0, "Saldo alto: mais de X meses de consumo"),
        "inadimplencia_critica_dias": (60.0, "Dias vencido para retirar UC do rateio"),
        "eficiencia_minima_pct": (0.85, "Eficiência de rateio mínima aceitável"),
        "vacancia_maxima_pct": (0.10, "Vacância máxima aceitável"),
        "inadimplencia_pct_max": (0.15, "Inadimplência do mês máx. sobre faturamento bruto"),
        "cobertura_minima_pct": (0.90, "Créditos utilizados / consumo mínimo"),
        "health_score_saudavel": (80.0, "Health score mínimo para status Saudável"),
        "health_score_atencao": (50.0, "Health score mínimo para status Atenção"),
        "desvio_geracao_alerta_pct": (0.15, "[ASSUMIDO] desvio de geração vs. média que dispara alerta"),
    }
    existentes = {p.chave: p.valor for p in db.query(m.ParametroAlerta).all()}
    for chave, (valor, desc) in defaults.items():
        if chave not in existentes:
            db.add(m.ParametroAlerta(chave=chave, valor=valor, descricao=desc))
    db.commit()
    return {p.chave: p.valor for p in db.query(m.ParametroAlerta).all()}


@dataclass
class IndicadoresUsina:
    usina_id: int
    competencia: date
    tarifa_liquida: float
    faturamento_potencial: float
    faturamento_bruto_realizado: float
    mrr_realizado: float
    inadimplencia_mes: float
    inadimplencia_acumulada: float
    eficiencia_rateio: float
    vacancia: float
    capturas_pendentes: int
    saldo_acumulado_kwh: float
    chamados_abertos: int


def tarifa_retorno_liquida(db: Session, usina: m.Usina) -> float:
    """TARIFAS!D/E/F/G x (1-desconto) x (1-%admin) — DASHBOARD!D10."""
    tarifa = (
        db.query(m.TarifaHistorico)
        .filter(m.TarifaHistorico.distribuidora_id == usina.distribuidora_id)
        .order_by(m.TarifaHistorico.vigencia_inicio.desc())
        .first()
    )
    if not tarifa:
        return 0.0
    if usina.modalidade == "Autoconsumo":
        bruta = tarifa.tarifa_injetada_autoconsumo_gd2 if usina.gd == 2 else tarifa.tarifa_injetada_autoconsumo_gd1
    else:
        bruta = tarifa.tarifa_injetada_gd2 if usina.gd == 2 else tarifa.tarifa_injetada_gd1
    return bruta * (1 - (usina.desconto_cliente or 0)) * (1 - (usina.pct_administracao or 0))


def energia_injetada(db: Session, usina_id: int, competencia: date) -> float:
    row = (
        db.query(func.sum(m.Geracao.energia_injetada_kwh))
        .filter(m.Geracao.usina_id == usina_id, m.Geracao.competencia == competencia)
        .scalar()
    )
    return float(row or 0.0)


def calcular_indicadores_usina(db: Session, usina: m.Usina, competencia: date) -> IndicadoresUsina:
    n = primeiro_dia_mes(competencia)
    n1 = edate(n, -1)

    tarifa_liq = tarifa_retorno_liquida(db, usina)
    energia_n1 = energia_injetada(db, usina.id, n1)
    # DASHBOARD!E10 — Faturamento/MRR Potencial usa a energia injetada de N-1
    faturamento_potencial = tarifa_liq * energia_n1

    uc_ids = [uc.id for uc in usina.ucs]

    # NOTA DE FIDELIDADE: a planilha soma estes agregados por NOME DA USINA no
    # EXTRATO inteiro (SUMIFS ... BH:BH, nome_usina), não pela lista de UCs
    # cadastradas no RATEIO — o extrato traz todas as contas ligadas à usina,
    # que costuma ser um universo maior que as UCs curadas no rateio. Por
    # isso os agregados abaixo usam `usina_extrato_id`, não `uc_id`.
    faturas_n_usina = (
        db.query(m.Fatura).filter(m.Fatura.usina_extrato_id == usina.id, m.Fatura.competencia == n).all()
    )

    # DASHBOARD!H10 — Fat. Bruto Realizado: visão CAIXA — soma o Total Pago
    # de toda fatura cuja DATA DE PAGAMENTO (não a competência da fatura!)
    # cai no mês N, líquido da concessionária quando Unificada=True. Isso é
    # deliberadamente diferente da competência de faturamento: uma fatura de
    # 05/2026 paga em 07/2026 conta como caixa de julho.
    inicio_mes, fim_mes = n, edate(n, 1)
    todas_faturas_usina_ = db.query(m.Fatura).filter(m.Fatura.usina_extrato_id == usina.id).all()
    faturas_pagas_no_mes = [
        f for f in todas_faturas_usina_ if f.data_pagamento and inicio_mes <= f.data_pagamento < fim_mes
    ]
    faturamento_bruto = sum(
        (f.total_pago - f.total_a_pagar_concessionaria) if f.unificada else f.total_pago
        for f in faturas_pagas_no_mes
    )
    mrr_realizado = faturamento_bruto * (usina.pct_administracao or 0)  # DASHBOARD!F10

    # ANALISES!E9/G9 — Eficiência de Rateio = créditos utilizados (N) / energia injetada (N-1)
    creditos_utilizados_n = sum(f.creditos_utilizados_kwh for f in faturas_n_usina)
    eficiencia = (creditos_utilizados_n / energia_n1) if energia_n1 else 0.0

    # ANALISES!I9/J9 — Vacância = (injetada N-1 - consumo de todas as contas em N,
    # exceto a UC âncora) / injetada N-1
    consumo_sem_ancora = sum(
        f.consumo_total_kwh for f in faturas_n_usina if f.numero_uc_extrato != usina.uc_ancora_numero
    )
    vacancia = ((energia_n1 - consumo_sem_ancora) / energia_n1) if energia_n1 else 0.0

    # ANALISES!K9/L9/M9 — Capturas pendentes = UCs *cadastradas no RATEIO*
    # que não aparecem no EXTRATO na competência N (aqui sim restrito às UCs
    # curadas — é sobre elas que a auditoria de captura faz sentido).
    ucs_com_fatura = {f.uc_id for f in faturas_n_usina if f.uc_id is not None}
    capturas_pendentes = len([uid for uid in uc_ids if uid not in ucs_com_fatura])

    # ANALISES!N9 — Saldo acumulado = soma do saldo de crédito solar das UCs do
    # rateio (não da usina inteira) na própria competência N
    saldo_acumulado = sum(f.saldo_credito_solar_kwh for f in faturas_n_usina if f.uc_id in uc_ids)

    # DASHBOARD!J10 — Inadimplência do mês: faturas Vencidas da usina cujo
    # vencimento Sunne cai no mês N
    faturas_vencidas_mes = [
        f for f in todas_faturas_usina_
        if f.status_pagamento == "Vencido" and f.vencimento_sunne and inicio_mes <= f.vencimento_sunne < fim_mes
    ]
    inadimplencia_mes = sum(
        (f.total_a_pagar_sunne - f.total_a_pagar_concessionaria) if f.unificada else f.total_a_pagar_sunne
        for f in faturas_vencidas_mes
    )
    # DASHBOARD!K10 — Inadimplência acumulada: todas as faturas Vencidas da usina, qualquer competência
    todas_vencidas = [f for f in todas_faturas_usina_ if f.status_pagamento == "Vencido"]
    inadimplencia_acumulada = sum(
        (f.total_a_pagar_sunne - f.total_a_pagar_concessionaria) if f.unificada else f.total_a_pagar_sunne
        for f in todas_vencidas
    )

    chamados_abertos = (
        db.query(m.Chamado)
        .filter(m.Chamado.usina_id == usina.id, m.Chamado.status.in_(["Aberto", "Em andamento"]))
        .count()
    )

    return IndicadoresUsina(
        usina_id=usina.id, competencia=n, tarifa_liquida=tarifa_liq,
        faturamento_potencial=faturamento_potencial, faturamento_bruto_realizado=faturamento_bruto,
        mrr_realizado=mrr_realizado, inadimplencia_mes=inadimplencia_mes,
        inadimplencia_acumulada=inadimplencia_acumulada, eficiencia_rateio=eficiencia, vacancia=vacancia,
        capturas_pendentes=capturas_pendentes, saldo_acumulado_kwh=saldo_acumulado, chamados_abertos=chamados_abertos,
    )


def health_score(ind: IndicadoresUsina, params: dict) -> float:
    """DASHBOARD!I10 — pesos 30/25/20/15/10, fiéis à planilha."""
    gap_neg = max(0.0, -(ind.mrr_realizado - ind.faturamento_potencial))
    p1 = 30 * (1 - min(1.0, max(0.0, gap_neg / max(ind.faturamento_potencial, 1))))
    p2 = 25 * (1 - min(1.0, max(0.0, ind.inadimplencia_mes / max(ind.faturamento_bruto_realizado, 1))))
    p3 = 20 * min(1.0, max(0.0, ind.eficiencia_rateio))
    p4 = 15 * (1 - min(1.0, max(0.0, ind.capturas_pendentes) / 10))
    p5 = 10 * (1 - min(1.0, max(0.0, ind.vacancia)))
    return max(0.0, min(100.0, p1 + p2 + p3 + p4 + p5))


def status_from_score(score: float, params: dict) -> str:
    if score >= params["health_score_saudavel"]:
        return "SAUDAVEL"
    if score >= params["health_score_atencao"]:
        return "ATENCAO"
    return "CRITICO"


def diagnostico(ind: IndicadoresUsina, params: dict) -> str:
    """DASHBOARD!R10 — concatenação textual dos pontos de atenção ativos."""
    bits = []
    if ind.chamados_abertos > 0:
        bits.append(f"{ind.chamados_abertos} chamado(s) aberto(s)")
    pct_inad = ind.inadimplencia_mes / max(ind.faturamento_bruto_realizado, 1)
    if pct_inad > params["inadimplencia_pct_max"]:
        bits.append(f"Inadimplência do mês alta ({pct_inad:.0%} do bruto)")
    if ind.capturas_pendentes > 0:
        bits.append(f"{ind.capturas_pendentes} UC(s) com fatura não capturada")
    if ind.eficiencia_rateio < params["eficiencia_minima_pct"]:
        bits.append(f"Eficiência de rateio baixa ({ind.eficiencia_rateio:.0%})")
    if ind.vacancia > params["vacancia_maxima_pct"]:
        bits.append(f"Vacância alta ({ind.vacancia:.0%}, sobra de energia sem cliente)")
    if ind.saldo_acumulado_kwh > 0:
        bits.append(f"Saldo acumulado nas UCs ({ind.saldo_acumulado_kwh:,.0f} kWh) — rebalancear rateio")
    return "; ".join(bits) if bits else "Sem ponto de atenção identificado"


# ---------------- Regras por UC (RATEIO!V/W/Z) ----------------

def uc_sinal_saldo(uc: m.UC, params: dict) -> str:
    if uc.autonomia_meses and uc.autonomia_meses > params["autonomia_maxima_meses"]:
        return f"SALDO ALTO - REBALANCEAR (AUTONOMIA {uc.autonomia_meses:.1f} MESES)"
    return "OK"


def uc_sinal_consumo(db: Session, uc: m.UC, competencia: date) -> str:
    n = primeiro_dia_mes(competencia)
    n1, n2 = edate(n, -1), edate(n, -2)

    def creditos_em(comp):
        f = db.query(m.Fatura).filter(m.Fatura.uc_id == uc.id, m.Fatura.competencia == comp).first()
        return f.creditos_utilizados_kwh if f else None

    s, t, u = creditos_em(n), creditos_em(n1), creditos_em(n2)
    if not uc.consumo_compensavel_kwh or s is None or t is None or u is None or s == 0 or t == 0 or u == 0:
        return "AGUARDAR - FALTA CAPTURA NOS 3 MESES"
    if s > uc.consumo_compensavel_kwh and t > uc.consumo_compensavel_kwh and u > uc.consumo_compensavel_kwh:
        return "RECEBENDO MAIS DO QUE PRECISA HA 3 MESES"
    if s < uc.consumo_compensavel_kwh and t < uc.consumo_compensavel_kwh and u < uc.consumo_compensavel_kwh:
        return "RECEBENDO MENOS DO QUE PRECISA HA 3 MESES"
    return "OK"


def uc_dias_vencido(db: Session, uc: m.UC, hoje: date) -> int:
    mais_antiga = (
        db.query(m.Fatura)
        .filter(m.Fatura.uc_id == uc.id, m.Fatura.status_pagamento == "Vencido")
        .order_by(m.Fatura.vencimento_sunne.asc())
        .first()
    )
    if not mais_antiga or not mais_antiga.vencimento_sunne:
        return 0
    return (hoje - mais_antiga.vencimento_sunne).days


def uc_sinal_inadimplencia(dias_vencido: int, params: dict) -> str:
    if dias_vencido > params["inadimplencia_critica_dias"]:
        return f"RETIRAR DO RATEIO - VENCIDO HA {dias_vencido} DIAS"
    return "OK"


# ---------------- Geração de alertas (upsert, idempotente) ----------------

def _upsert_alerta(db: Session, **kwargs):
    existente = (
        db.query(m.Alerta)
        .filter_by(categoria=kwargs["categoria"], usina_id=kwargs["usina_id"],
                   uc_id=kwargs.get("uc_id"), competencia=kwargs["competencia"])
        .first()
    )
    if existente:
        # Recálculo: atualiza os dados do indicador, mas preserva status/responsável/observação já preenchidos.
        for campo in ("problema_identificado", "indicador", "valor_atual", "valor_esperado", "acao_recomendada"):
            setattr(existente, campo, kwargs.get(campo))
        return existente
    novo = m.Alerta(**kwargs)
    db.add(novo)
    return novo


def gerar_alertas(db: Session, competencia: date, hoje: date = None) -> list:
    hoje = hoje or date.today()
    params = get_parametros(db)
    n = primeiro_dia_mes(competencia)
    gerados = []

    for usina in db.query(m.Usina).all():
        ind = calcular_indicadores_usina(db, usina, n)
        score = health_score(ind, params)
        status = status_from_score(score, params)

        if status != "SAUDAVEL":
            pct_inad = ind.inadimplencia_mes / max(ind.faturamento_bruto_realizado, 1)
            if pct_inad > params["inadimplencia_pct_max"]:
                gerados.append(_upsert_alerta(
                    db, categoria="Inadimplência", prioridade="critico", usina_id=usina.id, uc_id=None,
                    competencia=n, problema_identificado=f"Inadimplência do mês em {pct_inad:.0%} do bruto",
                    indicador="Inadimplência do mês", valor_atual=f"R$ {ind.inadimplencia_mes:,.2f}",
                    valor_esperado=f"< {params['inadimplencia_pct_max']:.0%}",
                    acao_recomendada="Acionar cliente / revisar carteira da usina"))
            if ind.eficiencia_rateio < params["eficiencia_minima_pct"]:
                gerados.append(_upsert_alerta(
                    db, categoria="Eficiência", prioridade="atencao", usina_id=usina.id, uc_id=None,
                    competencia=n, problema_identificado=f"Eficiência de rateio em {ind.eficiencia_rateio:.0%}",
                    indicador="Eficiência de Rateio", valor_atual=f"{ind.eficiencia_rateio:.0%}",
                    valor_esperado=f">= {params['eficiencia_minima_pct']:.0%}",
                    acao_recomendada="Investigar UCs com baixa utilização de crédito"))
            if ind.vacancia > params["vacancia_maxima_pct"]:
                gerados.append(_upsert_alerta(
                    db, categoria="Vacância", prioridade="atencao", usina_id=usina.id, uc_id=None,
                    competencia=n, problema_identificado=f"Vacância em {ind.vacancia:.0%}",
                    indicador="Vacância", valor_atual=f"{ind.vacancia:.0%}",
                    valor_esperado=f"<= {params['vacancia_maxima_pct']:.0%}",
                    acao_recomendada="Verificar disponibilidade de consumidores / âncora"))
        if ind.capturas_pendentes > 0:
            gerados.append(_upsert_alerta(
                db, categoria="Captura de Fatura", prioridade="critico", usina_id=usina.id, uc_id=None,
                competencia=n, problema_identificado=f"{ind.capturas_pendentes} UC(s) do rateio sem fatura na competência",
                indicador="Capturas Pendentes", valor_atual=str(ind.capturas_pendentes), valor_esperado="0",
                acao_recomendada="Abrir chamado de captura no HubSpot"))

        for uc in usina.ucs:
            sinal_saldo = uc_sinal_saldo(uc, params)
            if sinal_saldo != "OK":
                gerados.append(_upsert_alerta(
                    db, categoria="Saldo", prioridade="atencao", usina_id=usina.id, uc_id=uc.id, competencia=n,
                    problema_identificado=sinal_saldo, indicador="Autonomia",
                    valor_atual=f"{uc.autonomia_meses:.1f} meses", valor_esperado=f"<= {params['autonomia_maxima_meses']:.0f} meses",
                    acao_recomendada="Avaliar rebalanceamento de rateio"))
            sinal_consumo = uc_sinal_consumo(db, uc, n)
            if sinal_consumo != "OK":
                gerados.append(_upsert_alerta(
                    db, categoria="Rateio", prioridade="info" if sinal_consumo.startswith("AGUARDAR") else "medio",
                    usina_id=usina.id, uc_id=uc.id, competencia=n, problema_identificado=sinal_consumo,
                    indicador="Créditos x Consumo (3 meses)", valor_atual="—", valor_esperado="—",
                    acao_recomendada="Aguardar captura" if sinal_consumo.startswith("AGUARDAR") else "Ajustar alocação de rateio"))
            dias_vencido = uc_dias_vencido(db, uc, hoje)
            sinal_inad = uc_sinal_inadimplencia(dias_vencido, params)
            if sinal_inad != "OK":
                gerados.append(_upsert_alerta(
                    db, categoria="Inadimplência", prioridade="critico", usina_id=usina.id, uc_id=uc.id, competencia=n,
                    problema_identificado=sinal_inad, indicador="Dias vencido", valor_atual=f"{dias_vencido} dias",
                    valor_esperado=f"<= {params['inadimplencia_critica_dias']:.0f} dias",
                    acao_recomendada="Retirar UC do rateio"))

        for chamado in db.query(m.Chamado).filter(m.Chamado.usina_id == usina.id, m.Chamado.status.in_(["Aberto", "Em andamento"])).all():
            gerados.append(_upsert_alerta(
                db, categoria="Chamados", prioridade="medio", usina_id=usina.id, uc_id=None, competencia=n,
                problema_identificado=f"{chamado.tipo}: {chamado.descricao}", indicador="Status do chamado",
                valor_atual=chamado.status, valor_esperado="Resolvido", acao_recomendada="Cobrar retorno / atualizar status"))

    db.commit()
    return gerados

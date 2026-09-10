"""
Importação a partir do arquivo Excel que a operação já usa hoje.

Regra geral (pedido explícito, seção 26): nunca apagar dado histórico.
Toda escrita aqui é um upsert por chave natural:
  - Usina: nome
  - UC: (usina_id, numero)
  - Geração: (usina_id, competencia)
  - Fatura: (uc_id, competencia)
  - Tarifa: nova linha por distribuidora quando o valor muda (histórico versionado)
"""
from datetime import date, datetime
import openpyxl
from sqlalchemy.orm import Session

from . import models as m


def _to_date(v):
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return None


def _norm_mes(d):
    if d is None:
        return None
    return date(d.year, d.month, 1)


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _uc_num(v):
    """Normaliza número de UC para string sem casas decimais, seja qual for a
    forma como a planilha guardou (float, int ou texto) — evita que
    '4003278868.0' (RATEIO) e '4003278868' (EXTRATO) sejam tratados como
    UCs diferentes."""
    if v is None:
        return None
    if isinstance(v, float):
        return str(int(v))
    if isinstance(v, str) and v.replace(".", "", 1).isdigit() and v.endswith(".0"):
        return str(int(float(v)))
    return str(v).strip()


def import_tarifas(db: Session, path: str):
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb["TARIFAS"]
    hoje = date.today()
    count = 0
    for row in ws.iter_rows(min_row=3, max_row=ws.max_row):
        nome = row[0].value
        if not nome or not isinstance(nome, str):
            continue
        uf, fornecida, gd1, gd2, autogd1, autogd2 = (c.value for c in row[1:7])
        if fornecida is None:
            continue
        dist = db.query(m.Distribuidora).filter_by(nome=nome).first()
        if not dist:
            dist = m.Distribuidora(nome=nome, uf=uf)
            db.add(dist)
            db.flush()
        atual = (
            db.query(m.TarifaHistorico)
            .filter_by(distribuidora_id=dist.id)
            .order_by(m.TarifaHistorico.vigencia_inicio.desc())
            .first()
        )
        mudou = not atual or (
            atual.tarifa_fornecida, atual.tarifa_injetada_gd1, atual.tarifa_injetada_gd2,
            atual.tarifa_injetada_autoconsumo_gd1, atual.tarifa_injetada_autoconsumo_gd2,
        ) != (fornecida, gd1, gd2, autogd1, autogd2)
        if mudou:
            db.add(m.TarifaHistorico(
                distribuidora_id=dist.id, vigencia_inicio=hoje, tarifa_fornecida=_num(fornecida),
                tarifa_injetada_gd1=_num(gd1), tarifa_injetada_gd2=_num(gd2),
                tarifa_injetada_autoconsumo_gd1=_num(autogd1), tarifa_injetada_autoconsumo_gd2=_num(autogd2),
            ))
            count += 1
    db.commit()
    return count


def import_rateio(db: Session, path: str):
    """Bloco 1 (config de usinas, linhas 4-24) + Bloco 2 (UCs, linha 27 em diante)."""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb["RATEIO"]

    usinas_por_linha = {}
    for r in range(4, 25):
        nome = ws.cell(row=r, column=1).value
        if not nome:
            continue
        pct_admin = _num(ws.cell(row=r, column=2).value)
        uc_ancora = ws.cell(row=r, column=3).value
        distribuidora_nome = ws.cell(row=r, column=4).value
        desconto = _num(ws.cell(row=r, column=5).value)
        gd = ws.cell(row=r, column=9).value
        modalidade = ws.cell(row=r, column=10).value or "Compartilhada"  # [ASSUMIDO] manual — Fase 1.6.3

        dist = db.query(m.Distribuidora).filter_by(nome=distribuidora_nome).first()
        usina = db.query(m.Usina).filter_by(nome=nome).first()
        if not usina:
            usina = m.Usina(nome=nome)
            db.add(usina)
        usina.pct_administracao = pct_admin
        usina.desconto_cliente = desconto
        usina.uc_ancora_numero = _uc_num(uc_ancora)
        usina.distribuidora_id = dist.id if dist else None
        usina.uf = dist.uf if dist else None
        usina.gd = int(gd) if gd else None
        usina.modalidade = modalidade
        db.flush()
        usinas_por_linha[r] = usina

    n_usinas_config = 4  # linha 4 é a primeira; RATEIO!A4 == RATEIO!A27 na aba original
    usina_ref = usinas_por_linha.get(n_usinas_config)

    count_ucs = 0
    for r in range(27, ws.max_row + 1):
        nome_usina = ws.cell(row=r, column=1).value
        if not nome_usina:
            continue
        usina = db.query(m.Usina).filter_by(nome=nome_usina).first()
        if not usina:
            continue
        numero = _uc_num(ws.cell(row=r, column=3).value)
        apelido = ws.cell(row=r, column=4).value
        cnpj = ws.cell(row=r, column=5).value
        consumo = _num(ws.cell(row=r, column=6).value)
        saldo = _num(ws.cell(row=r, column=7).value)
        vida_util = _num(ws.cell(row=r, column=8).value)
        rateio_ideal = _num(ws.cell(row=r, column=9).value)
        rateio_verificado = _num(ws.cell(row=r, column=10).value)
        est_verificado = _num(ws.cell(row=r, column=12).value)
        est_ideal = _num(ws.cell(row=r, column=13).value)
        deficit = _num(ws.cell(row=r, column=14).value)
        autonomia = _num(ws.cell(row=r, column=15).value)
        obs = ws.cell(row=r, column=16).value

        uc = db.query(m.UC).filter_by(usina_id=usina.id, numero=numero).first()
        if not uc:
            uc = m.UC(usina_id=usina.id, numero=numero)
            db.add(uc)
        uc.apelido = apelido
        uc.cnpj = cnpj
        uc.consumo_compensavel_kwh = consumo
        uc.saldo_kwh = saldo
        uc.vida_util_saldo_meses = vida_util
        uc.rateio_ideal_pct = rateio_ideal
        uc.rateio_verificado_pct = rateio_verificado
        uc.estimado_verificado_kwh = est_verificado
        uc.estimado_ideal_kwh = est_ideal
        uc.deficit_mensal_kwh = deficit
        uc.autonomia_meses = autonomia
        uc.observacoes = obs
        uc.is_ancora = (usina.uc_ancora_numero == numero)
        count_ucs += 1
    db.commit()
    return {"usinas": len(usinas_por_linha), "ucs": count_ucs}


def import_geracao(db: Session, path: str):
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb["GERACAO"]
    count = 0
    for row in ws.iter_rows(min_row=3, max_row=ws.max_row):
        nome_usina = row[0].value
        if not nome_usina:
            continue
        usina = db.query(m.Usina).filter_by(nome=nome_usina).first()
        if not usina:
            continue
        competencia = _norm_mes(_to_date(row[2].value))
        if not competencia:
            continue
        registro = db.query(m.Geracao).filter_by(usina_id=usina.id, competencia=competencia).first()
        if not registro:
            registro = m.Geracao(usina_id=usina.id, competencia=competencia)
            db.add(registro)
        registro.vencimento = _to_date(row[3].value)
        registro.modelo_tarifario = row[4].value
        registro.total_a_pagar = row[5].value if isinstance(row[5].value, (int, float)) else None
        registro.energia_injetada_kwh = _num(row[6].value)
        registro.energia_distribuida_kwh = _num(row[7].value)
        registro.saldo_kwh = _num(row[8].value)
        registro.observacao = row[9].value if len(row) > 9 else None
        count += 1
    db.commit()
    return count


def import_extrato(db: Session, path: str):
    """
    Importa TODA linha do extrato, mesmo quando a conta não é uma das UCs
    curadas na aba RATEIO — é assim que a planilha original calcula
    Eficiência de Rateio, Vacância, Faturamento Bruto e Inadimplência
    (SUMIFS por nome de usina no EXTRATO inteiro, não pela lista do RATEIO).
    `uc_id` fica nulo quando a conta não bate com nenhuma UC cadastrada;
    isso não impede a importação, só limita os relatórios "por UC".
    """
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb["EXTRATO_DETALHADO"]
    count, sem_uc, sem_usina = 0, 0, 0
    for row in ws.iter_rows(min_row=3, max_row=ws.max_row):
        numero_conta = _uc_num(row[0].value)  # coluna A — pode ser número de UC ou CPF/CNPJ, conforme a distribuidora
        if numero_conta is None:
            continue
        usina_extrato_nome = row[59].value  # BH
        if not usina_extrato_nome:
            sem_usina += 1
            continue
        usina_extrato = db.query(m.Usina).filter_by(nome=usina_extrato_nome).first()
        if not usina_extrato:
            sem_usina += 1
            continue  # usina citada no extrato ainda não existe no cadastro (RATEIO) — nada a fazer até ela ser cadastrada

        competencia = _norm_mes(_to_date(row[4].value))  # E
        if not competencia:
            continue

        uc = db.query(m.UC).filter_by(numero=numero_conta).first()
        if not uc:
            sem_uc += 1

        fatura = (
            db.query(m.Fatura)
            .filter_by(usina_extrato_id=usina_extrato.id, numero_uc_extrato=numero_conta, competencia=competencia)
            .first()
        )
        if not fatura:
            fatura = m.Fatura(usina_extrato_id=usina_extrato.id, numero_uc_extrato=numero_conta, competencia=competencia)
            db.add(fatura)
        fatura.uc_id = uc.id if uc else None
        fatura.vencimento_sunne = _to_date(row[7].value)  # H
        fatura.titular = row[2].value  # C
        fatura.consumo_total_kwh = _num(row[18].value)  # S
        fatura.saldo_credito_solar_kwh = _num(row[19].value)  # T
        fatura.creditos_utilizados_kwh = _num(row[20].value)  # U
        fatura.creditos_recebidos_kwh = _num(row[21].value)  # V
        fatura.total_a_pagar_sunne = _num(row[28].value)  # AC
        fatura.total_pago = _num(row[30].value)  # AE
        fatura.status_pagamento = row[36].value  # AK
        fatura.data_pagamento = _to_date(row[38].value)  # AM
        fatura.total_a_pagar_concessionaria = _num(row[50].value)  # AY
        fatura.unificada = bool(row[62].value) if row[62].value is not None else False  # BK
        count += 1
    db.commit()
    return {"faturas_importadas": count, "contas_sem_uc_cadastrada_no_rateio": sem_uc, "linhas_sem_usina_cadastrada": sem_usina}


def import_chamados(db: Session, path: str):
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb["CHAMADOS"]
    count = 0
    for row in ws.iter_rows(min_row=4, max_row=ws.max_row):
        nome_usina_ou_sigla, tipo, descricao = row[1].value, row[2].value, row[3].value
        if not tipo:
            continue
        usina = db.query(m.Usina).filter(m.Usina.nome.ilike(f"%{nome_usina_ou_sigla}%")).first() if nome_usina_ou_sigla else None
        if not usina:
            continue
        chamado = m.Chamado(
            usina_id=usina.id, tipo=tipo, descricao=descricao,
            ucs_afetadas=str(row[4].value) if row[4].value else None,
            qtd_ucs=int(row[5].value) if row[5].value else 0,
            competencia_afetada=_norm_mes(_to_date(row[6].value)),
            data_abertura=_to_date(row[7].value),
            status=row[8].value or "Aberto",
            impacto_mrr=_num(row[9].value),
            observacao=row[10].value if len(row) > 10 else None,
        )
        db.add(chamado)
        count += 1
    db.commit()
    return count


def importar_tudo(db: Session, path: str) -> dict:
    return {
        "tarifas": import_tarifas(db, path),
        "rateio": import_rateio(db, path),
        "geracao": import_geracao(db, path),
        "extrato": import_extrato(db, path),
        "chamados": import_chamados(db, path),
    }

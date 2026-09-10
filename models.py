from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Date, DateTime, ForeignKey, UniqueConstraint
)
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base


class Distribuidora(Base):
    __tablename__ = "distribuidoras"
    id = Column(Integer, primary_key=True)
    nome = Column(String, unique=True, nullable=False)
    uf = Column(String(2))
    tarifas = relationship("TarifaHistorico", back_populates="distribuidora", order_by="TarifaHistorico.vigencia_inicio")


class TarifaHistorico(Base):
    """Nunca é sobrescrita: cada alteração de tarifa gera uma nova linha com vigencia_inicio."""
    __tablename__ = "tarifas_historico"
    id = Column(Integer, primary_key=True)
    distribuidora_id = Column(Integer, ForeignKey("distribuidoras.id"), nullable=False)
    vigencia_inicio = Column(Date, nullable=False)
    tarifa_fornecida = Column(Float, nullable=False)
    tarifa_injetada_gd1 = Column(Float, nullable=False)
    tarifa_injetada_gd2 = Column(Float, nullable=False)
    tarifa_injetada_autoconsumo_gd1 = Column(Float, nullable=False)
    tarifa_injetada_autoconsumo_gd2 = Column(Float, nullable=False)
    distribuidora = relationship("Distribuidora", back_populates="tarifas")


class Usina(Base):
    __tablename__ = "usinas"
    id = Column(Integer, primary_key=True)
    nome = Column(String, unique=True, nullable=False)
    numero_ug = Column(String)
    distribuidora_id = Column(Integer, ForeignKey("distribuidoras.id"))
    uf = Column(String(2))
    uc_ancora_numero = Column(String)
    gd = Column(Integer)  # 1 ou 2
    modalidade = Column(String)  # "Compartilhada" | "Autoconsumo" — [ASSUMIDO] manual (ver Fase 1.6.3)
    pct_administracao = Column(Float, nullable=False, default=0.0)
    desconto_cliente = Column(Float, nullable=False, default=0.0)
    status = Column(String, default="Ativa")
    responsavel = Column(String)

    distribuidora = relationship("Distribuidora")
    ucs = relationship("UC", back_populates="usina")
    geracoes = relationship("Geracao", back_populates="usina")
    chamados = relationship("Chamado", back_populates="usina")


class UC(Base):
    __tablename__ = "ucs"
    id = Column(Integer, primary_key=True)
    usina_id = Column(Integer, ForeignKey("usinas.id"), nullable=False)
    numero = Column(String, nullable=False, index=True)
    apelido = Column(String)
    cnpj = Column(String)
    consumo_compensavel_kwh = Column(Float, default=0.0)
    saldo_kwh = Column(Float, default=0.0)
    vida_util_saldo_meses = Column(Float, default=0.0)
    rateio_ideal_pct = Column(Float)
    rateio_verificado_pct = Column(Float)
    estimado_verificado_kwh = Column(Float)
    estimado_ideal_kwh = Column(Float)
    deficit_mensal_kwh = Column(Float)
    autonomia_meses = Column(Float, default=0.0)  # [ASSUMIDO] recebido, não recalculado (ver Fase 1.6.2)
    observacoes = Column(String)
    is_ancora = Column(Boolean, default=False)

    usina = relationship("Usina", back_populates="ucs")
    faturas = relationship("Fatura", back_populates="uc")

    __table_args__ = (UniqueConstraint("usina_id", "numero", name="uq_uc_por_usina"),)


class Geracao(Base):
    __tablename__ = "geracao"
    id = Column(Integer, primary_key=True)
    usina_id = Column(Integer, ForeignKey("usinas.id"), nullable=False)
    competencia = Column(Date, nullable=False)  # sempre normalizada para dia 1 do mês
    vencimento = Column(Date)
    modelo_tarifario = Column(String)
    total_a_pagar = Column(Float)
    energia_injetada_kwh = Column(Float, default=0.0)
    energia_distribuida_kwh = Column(Float, default=0.0)
    saldo_kwh = Column(Float, default=0.0)
    observacao = Column(String)

    usina = relationship("Usina", back_populates="geracoes")
    __table_args__ = (UniqueConstraint("usina_id", "competencia", name="uq_geracao_usina_competencia"),)


class Fatura(Base):
    """
    Import direto do EXTRATO_DETALHADO.

    IMPORTANTE (fidelidade com a planilha): o EXTRATO traz TODAS as contas
    ligadas à usina (BH), não só as UCs curadas na aba RATEIO — a planilha
    original calcula Eficiência de Rateio, Vacância, Faturamento Bruto e
    Inadimplência somando por NOME DA USINA no extrato inteiro, não pela
    lista de UCs do rateio. `uc_id` só é preenchido quando o número da conta
    bate com uma UC cadastrada no RATEIO; `usina_extrato_id` é sempre
    preenchido e é o que os agregados por usina devem usar.
    """
    __tablename__ = "faturas"
    id = Column(Integer, primary_key=True)
    uc_id = Column(Integer, ForeignKey("ucs.id"), nullable=True)
    numero_uc_extrato = Column(String)  # identificador bruto da conta no extrato (pode ser UC ou CPF/CNPJ, conforme a distribuidora)
    usina_extrato_id = Column(Integer, ForeignKey("usinas.id"), nullable=False)  # usina conforme cadastrada NO EXTRATO
    competencia = Column(Date, nullable=False)
    vencimento_sunne = Column(Date)
    emissao = Column(Date)
    titular = Column(String)
    consumo_total_kwh = Column(Float, default=0.0)
    saldo_credito_solar_kwh = Column(Float, default=0.0)
    creditos_utilizados_kwh = Column(Float, default=0.0)
    creditos_recebidos_kwh = Column(Float, default=0.0)
    total_a_pagar_sunne = Column(Float, default=0.0)
    total_pago = Column(Float, default=0.0)
    status_pagamento = Column(String)  # "Pago" | "Vencido" | ...
    data_pagamento = Column(Date)
    total_a_pagar_concessionaria = Column(Float, default=0.0)
    vencimento_concessionaria = Column(Date)
    unificada = Column(Boolean, default=False)

    uc = relationship("UC", back_populates="faturas")
    __table_args__ = (
        UniqueConstraint("usina_extrato_id", "numero_uc_extrato", "competencia", name="uq_fatura_conta_competencia"),
    )


class Chamado(Base):
    __tablename__ = "chamados"
    id = Column(Integer, primary_key=True)
    usina_id = Column(Integer, ForeignKey("usinas.id"), nullable=False)
    tipo = Column(String)
    descricao = Column(String)
    ucs_afetadas = Column(String)
    qtd_ucs = Column(Integer, default=0)
    competencia_afetada = Column(Date)
    data_abertura = Column(Date)
    status = Column(String, default="Aberto")  # Aberto|Em andamento|Aguardando retorno|Resolvido|Encerrado
    impacto_mrr = Column(Float, default=0.0)
    observacao = Column(String)

    usina = relationship("Usina", back_populates="chamados")


class Alerta(Base):
    """Upsert por (categoria, usina_id, uc_id, competencia) — recálculo nunca duplica, preserva status/responsável."""
    __tablename__ = "alertas"
    id = Column(Integer, primary_key=True)
    categoria = Column(String, nullable=False)
    prioridade = Column(String, nullable=False)  # critico|atencao|medio|info
    usina_id = Column(Integer, ForeignKey("usinas.id"), nullable=False)
    uc_id = Column(Integer, ForeignKey("ucs.id"), nullable=True)
    competencia = Column(Date, nullable=False)
    problema_identificado = Column(String)
    indicador = Column(String)
    valor_atual = Column(String)
    valor_esperado = Column(String)
    diferenca = Column(String)
    acao_recomendada = Column(String)
    status = Column(String, default="Pendente")  # Pendente|Em análise|Aguardando terceiro|Resolvido|Encerrado
    responsavel = Column(String)
    prazo = Column(Date)
    observacao = Column(String)
    criado_em = Column(DateTime, default=datetime.utcnow)
    resolvido_em = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("categoria", "usina_id", "uc_id", "competencia", name="uq_alerta_natural_key"),
    )


class Tarefa(Base):
    __tablename__ = "tarefas"
    id = Column(Integer, primary_key=True)
    alerta_id = Column(Integer, ForeignKey("alertas.id"), nullable=True)
    prioridade = Column(String, nullable=False)
    categoria = Column(String)
    usina_id = Column(Integer, ForeignKey("usinas.id"))
    uc_id = Column(Integer, ForeignKey("ucs.id"), nullable=True)
    descricao = Column(String)
    acao_recomendada = Column(String)
    prazo = Column(Date)
    status = Column(String, default="Aberta")  # Aberta|Em andamento|Concluída
    concluida_em = Column(DateTime, nullable=True)
    concluida_por = Column(String)


class AuditoriaHistorico(Base):
    """Snapshot mensal por usina — gravado ao fechar uma competência."""
    __tablename__ = "auditorias_historico"
    id = Column(Integer, primary_key=True)
    usina_id = Column(Integer, ForeignKey("usinas.id"), nullable=False)
    competencia = Column(Date, nullable=False)
    health_score = Column(Float)
    eficiencia_rateio = Column(Float)
    vacancia = Column(Float)
    capturas_pendentes = Column(Integer)
    saldo_acumulado_kwh = Column(Float)
    inadimplencia_mes = Column(Float)
    inadimplencia_acumulada = Column(Float)
    faturamento_potencial = Column(Float)
    mrr_realizado = Column(Float)
    status = Column(String)
    gerado_em = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("usina_id", "competencia", name="uq_auditoria_usina_competencia"),)


class ParametroAlerta(Base):
    """Parâmetros configuráveis — defaults extraídos da planilha (Fase 1.5)."""
    __tablename__ = "parametros_alerta"
    chave = Column(String, primary_key=True)
    valor = Column(Float, nullable=False)
    descricao = Column(String)
    atualizado_em = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

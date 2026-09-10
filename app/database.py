import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Produção: defina DATABASE_URL como uma URL Postgres, ex.:
#   postgresql+psycopg2://usuario:senha@host:5432/auditoria_solar
# Dev/demo: usa SQLite em arquivo, zero configuração.
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./auditoria_solar.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

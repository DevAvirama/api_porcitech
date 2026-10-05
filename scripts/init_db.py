#!/usr/bin/env python3
"""
Script de Inicialización de Base de Datos - PorciTech API
Lee las variables de entorno, verifica/crea la base de datos PostgreSQL
y ejecuta el esquema DDL y datos semilla de `scripts/init.sql`.
"""

import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
from dotenv import load_dotenv

# Cargar .env desde la raíz del backend
BACKEND_DIR = Path(__file__).resolve().parent.parent
env_path = BACKEND_DIR / ".env"
load_dotenv(dotenv_path=env_path)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/porcitech_db"
)

# Convertir URI SQLAlchemy a URI asyncpg nativa
clean_url = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
parsed = urlparse(clean_url)

DB_USER = parsed.username or "postgres"
DB_PASSWORD = parsed.password or "postgres"
DB_HOST = parsed.hostname or "localhost"
DB_PORT = parsed.port or 5432
TARGET_DB = parsed.path.lstrip("/") or "porcitech_db"

INIT_SQL_PATH = BACKEND_DIR / "scripts" / "init.sql"


async def ensure_database_exists():
    """Conecta a la base de datos 'postgres' y crea la base destino si no existe."""
    print(f"[*] Conectando a PostgreSQL ({DB_HOST}:{DB_PORT}) para verificar la base de datos '{TARGET_DB}'...")
    try:
        conn = await asyncpg.connect(
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT,
            database="postgres"
        )
    except Exception as e:
        print(f"[-] No se pudo conectar a la base de datos 'postgres' como {DB_USER}: {e}")
        # Intentar conectar con usuario santiago o sin password si trust
        try:
            conn = await asyncpg.connect(
                user=os.getenv("USER", "postgres"),
                host=DB_HOST,
                port=DB_PORT,
                database="postgres"
            )
        except Exception as e2:
            print(f"[!] Error crítico de conexión: {e2}")
            raise

    try:
        db_exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", TARGET_DB
        )
        if not db_exists:
            print(f"[*] La base de datos '{TARGET_DB}' no existe. Creándola...")
            await conn.execute(f'CREATE DATABASE "{TARGET_DB}";')
            print(f"[+] Base de datos '{TARGET_DB}' creada exitosamente.")
        else:
            print(f"[✓] Base de datos '{TARGET_DB}' confirmada.")
    finally:
        await conn.close()


async def execute_init_sql():
    """Ejecuta init.sql en la base de datos destino."""
    if not INIT_SQL_PATH.exists():
        print(f"[-] Error: Archivo DDL no encontrado en {INIT_SQL_PATH}")
        sys.exit(1)

    print(f"[*] Leyendo script SQL: {INIT_SQL_PATH}...")
    sql_content = INIT_SQL_PATH.read_text(encoding="utf-8")

    print(f"[*] Conectando a '{TARGET_DB}' para aplicar DDL y semillas...")
    conn = await asyncpg.connect(
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT,
        database=TARGET_DB
    )

    try:
        print("[*] Ejecutando DDL (extensiones, tablas, hipertabla, vistas continuas y semillas)...")
        await conn.execute(sql_content)
        print("[+] Script DDL ejecutado con éxito.")

        # Validación de tablas creadas y conteo de registros
        print("\n" + "=" * 60)
        print(" RESUMEN DE TABLAS Y REGISTROS APROVISIONADOS (PORCITECH)")
        print("=" * 60)
        tables = [
            "usuarios",
            "corrales",
            "animales",
            "inventario_categorias",
            "inventario_items",
            "protocolos_bioseguridad",
            "registro_pesos",
        ]
        for tbl in tables:
            count = await conn.fetchval(f"SELECT count(*) FROM {tbl};")
            print(f"  • {tbl.ljust(26)} : {count} registros")
        
        # Validar vistas analíticas
        cagg_count = await conn.fetchval("SELECT count(*) FROM cagg_pesos_diarios_corral;")
        print(f"  • {'cagg_pesos_diarios_corral'.ljust(26)} : {cagg_count} registros (TimescaleDB)")
        print("=" * 60)
        print("[✓] Inicialización completada exitosamente sin errores.\n")

    except Exception as e:
        print(f"[-] Error al ejecutar DDL: {e}")
        raise
    finally:
        await conn.close()


async def main():
    print("=" * 60)
    print(" INICIALIZADOR DE BASE DE DATOS - PORCITECH API")
    print("=" * 60)
    await ensure_database_exists()
    await execute_init_sql()


if __name__ == "__main__":
    asyncio.run(main())

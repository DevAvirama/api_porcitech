"""
Idempotencia Offline-First basada en `id_local` (UUID v4 generado en el móvil).

El patrón "consultar si existe -> insertar" no es atómico: dos reintentos simultáneos
de la cola FIFO podrían pasar ambos la consulta y duplicar el registro. Para evitarlo
se toma un candado transaccional de PostgreSQL (`pg_advisory_xact_lock`) indexado por
el `id_local`. El candado se libera automáticamente al hacer COMMIT o ROLLBACK, por lo
que la segunda petición espera, vuelve a consultar y encuentra el registro ya creado.

Esto funciona también para `registro_pesos`, que es hipertabla TimescaleDB y no admite
un índice único sobre `id_local` sin incluir la columna de partición `tiempo`.
"""

import uuid
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def lock_id_local(db: AsyncSession, id_local: Optional[uuid.UUID]) -> None:
    """Serializa las peticiones concurrentes que comparten el mismo `id_local`."""
    if id_local is None:
        return
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"porcitech:id_local:{id_local}"},
    )

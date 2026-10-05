import uuid
from decimal import Decimal
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user_optional, get_db
from app.models.inventario import (
    InventarioCategoria,
    InventarioItem,
    InventarioLote,
    MovimientoInventario,
)
from app.models.user import Usuario
from app.schemas.inventario import (
    InventarioCategoriaResponse,
    InventarioItemCreate,
    InventarioItemDetailResponse,
    InventarioItemResponse,
    InventarioLoteResponse,
    MovimientoInventarioCreate,
    MovimientoInventarioResponse,
)

router = APIRouter()


async def get_fallback_user_id(db: AsyncSession, current_user: Optional[Usuario] = None) -> uuid.UUID:
    if current_user:
        return current_user.id
    query = select(Usuario.id).where(Usuario.activo.is_(True), Usuario.deleted_at.is_(None))
    result = await db.execute(query.limit(1))
    user_id = result.scalar_one_or_none()
    if user_id:
        return user_id
    return uuid.UUID("b1eebc99-9c0b-4ef8-bb6d-6bb9bd380a22")


@router.get("/categorias", response_model=List[InventarioCategoriaResponse])
async def list_categorias(db: AsyncSession = Depends(get_db)):
    """
    Retorna la lista completa de categorías de inventario ordenadas alfabéticamente.
    """
    query = select(InventarioCategoria).order_by(InventarioCategoria.nombre.asc())
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/items", response_model=List[InventarioItemResponse])
async def list_items(
    categoria_id: Optional[uuid.UUID] = Query(None, description="Filtrar por categoría"),
    search: Optional[str] = Query(None, description="Búsqueda por nombre o SKU"),
    bajo_stock: Optional[bool] = Query(None, description="Filtrar insumos bajo stock mínimo"),
    db: AsyncSession = Depends(get_db),
):
    """
    Lista insumos de bodega con filtros opcionales de categoría, búsqueda y alerta de stock.
    """
    query = (
        select(InventarioItem)
        .where(InventarioItem.deleted_at.is_(None))
        .order_by(InventarioItem.nombre.asc())
    )

    if categoria_id:
        query = query.where(InventarioItem.categoria_id == categoria_id)

    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.where(
            or_(
                InventarioItem.nombre.ilike(term),
                InventarioItem.codigo_sku.ilike(term),
            )
        )

    if bajo_stock is True:
        query = query.where(InventarioItem.stock_actual <= InventarioItem.stock_minimo)

    result = await db.execute(query)
    items = result.scalars().all()

    response_items = []
    for item in items:
        response_items.append(
            InventarioItemResponse(
                id=item.id,
                categoria_id=item.categoria_id,
                categoria_codigo=item.categoria.codigo if item.categoria else None,
                categoria_nombre=item.categoria.nombre if item.categoria else None,
                codigo_sku=item.codigo_sku,
                nombre=item.nombre,
                unidad_medida=item.unidad_medida,
                stock_actual=float(item.stock_actual),
                stock_minimo=float(item.stock_minimo),
                costo_unitario=float(item.costo_unitario),
                ubicacion_bodega=item.ubicacion_bodega,
                sync_status=item.sync_status,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
        )
    return response_items


@router.post("/items", response_model=InventarioItemResponse, status_code=status.HTTP_201_CREATED)
async def create_item(
    item_in: InventarioItemCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[Usuario] = Depends(get_current_user_optional),
):
    """
    Registra un nuevo artículo de inventario en la base de datos PostgreSQL.
    """
    # Validar SKU único
    check_sku = select(InventarioItem).where(
        InventarioItem.codigo_sku == item_in.codigo_sku,
        InventarioItem.deleted_at.is_(None),
    )
    res_sku = await db.execute(check_sku)
    if res_sku.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Ya existe un artículo con el código SKU '{item_in.codigo_sku}'",
        )

    # Validar categoría existente
    cat_query = select(InventarioCategoria).where(InventarioCategoria.id == item_in.categoria_id)
    cat_res = await db.execute(cat_query)
    categoria = cat_res.scalar_one_or_none()
    if not categoria:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Categoría con id '{item_in.categoria_id}' no encontrada",
        )

    new_item = InventarioItem(
        categoria_id=item_in.categoria_id,
        codigo_sku=item_in.codigo_sku.strip(),
        nombre=item_in.nombre.strip(),
        unidad_medida=item_in.unidad_medida.strip(),
        stock_actual=Decimal(str(item_in.stock_actual or 0.0)),
        stock_minimo=Decimal(str(item_in.stock_minimo or 0.0)),
        costo_unitario=Decimal(str(item_in.costo_unitario or 0.0)),
        ubicacion_bodega=item_in.ubicacion_bodega.strip() if item_in.ubicacion_bodega else None,
        sync_status="synced",
    )
    db.add(new_item)
    await db.commit()
    await db.refresh(new_item)

    return InventarioItemResponse(
        id=new_item.id,
        categoria_id=new_item.categoria_id,
        categoria_codigo=categoria.codigo,
        categoria_nombre=categoria.nombre,
        codigo_sku=new_item.codigo_sku,
        nombre=new_item.nombre,
        unidad_medida=new_item.unidad_medida,
        stock_actual=float(new_item.stock_actual),
        stock_minimo=float(new_item.stock_minimo),
        costo_unitario=float(new_item.costo_unitario),
        ubicacion_bodega=new_item.ubicacion_bodega,
        sync_status=new_item.sync_status,
        created_at=new_item.created_at,
        updated_at=new_item.updated_at,
    )


@router.get("/items/{id}", response_model=InventarioItemDetailResponse)
async def get_item_detail(id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    Retorna el detalle completo de un artículo de inventario junto con sus lotes registrados.
    """
    query = (
        select(InventarioItem)
        .options(selectinload(InventarioItem.lotes))
        .where(InventarioItem.id == id, InventarioItem.deleted_at.is_(None))
    )
    result = await db.execute(query)
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Insumo con id '{id}' no encontrado",
        )

    lotes_resp = [
        InventarioLoteResponse(
            id=l.id,
            item_id=l.item_id,
            numero_lote=l.numero_lote,
            fecha_fabricacion=l.fecha_fabricacion,
            fecha_vencimiento=l.fecha_vencimiento,
            cantidad_inicial=float(l.cantidad_inicial),
            cantidad_actual=float(l.cantidad_disponible),
            cantidad_disponible=float(l.cantidad_disponible),
            registro_ica=l.registro_ica,
            created_at=l.created_at,
        )
        for l in item.lotes
    ]

    return InventarioItemDetailResponse(
        id=item.id,
        categoria_id=item.categoria_id,
        categoria_codigo=item.categoria.codigo if item.categoria else None,
        categoria_nombre=item.categoria.nombre if item.categoria else None,
        codigo_sku=item.codigo_sku,
        nombre=item.nombre,
        unidad_medida=item.unidad_medida,
        stock_actual=float(item.stock_actual),
        stock_minimo=float(item.stock_minimo),
        costo_unitario=float(item.costo_unitario),
        ubicacion_bodega=item.ubicacion_bodega,
        sync_status=item.sync_status,
        created_at=item.created_at,
        updated_at=item.updated_at,
        lotes=lotes_resp,
    )


@router.get("/movimientos", response_model=List[MovimientoInventarioResponse])
async def list_movimientos(
    limite: int = Query(20, ge=1, le=500),
    item_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Retorna el historial de movimientos de bodega (Kardex) ordenados de forma descendente por fecha.
    """
    query = (
        select(MovimientoInventario)
        .order_by(MovimientoInventario.fecha_movimiento.desc())
        .limit(limite)
    )

    if item_id:
        query = query.where(MovimientoInventario.item_id == item_id)

    result = await db.execute(query)
    movimientos = result.scalars().all()

    resp = []
    for m in movimientos:
        user_name = None
        if m.usuario:
            user_name = f"{m.usuario.nombre} {m.usuario.apellido}".strip()

        resp.append(
            MovimientoInventarioResponse(
                id=m.id,
                item_id=m.item_id,
                item_nombre=m.item.nombre if m.item else None,
                lote_id=m.lote_inventario_id,
                tipo_movimiento=m.tipo_movimiento,
                cantidad=float(m.cantidad),
                costo_unitario=float(m.costo_unitario),
                costo_total=float(m.costo_unitario * m.cantidad),
                fecha_movimiento=m.fecha_movimiento,
                motivo=m.motivo,
                usuario_id=m.usuario_id,
                usuario_nombre=user_name,
                sync_status=m.sync_status,
            )
        )
    return resp


@router.post("/movimientos", response_model=MovimientoInventarioResponse, status_code=status.HTTP_201_CREATED)
async def register_movimiento(
    mov_in: MovimientoInventarioCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[Usuario] = Depends(get_current_user_optional),
):
    """
    Registra una entrada, salida o ajuste en el Kardex y actualiza atómicamente el stock del ítem.
    """
    # Buscar ítem y bloquear para consistencia en concurrencia
    query_item = (
        select(InventarioItem)
        .where(InventarioItem.id == mov_in.item_id, InventarioItem.deleted_at.is_(None))
        .with_for_update(of=InventarioItem)
    )
    res_item = await db.execute(query_item)
    item = res_item.scalar_one_or_none()
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Insumo con id '{mov_in.item_id}' no encontrado",
        )

    # Identificar lote si aplica
    target_lote_id = mov_in.lote_id or mov_in.lote_inventario_id

    # Determinar costo unitario y total
    costo_unit = Decimal(str(mov_in.costo_unitario)) if mov_in.costo_unitario is not None else item.costo_unitario
    cantidad_dec = Decimal(str(mov_in.cantidad))
    costo_total = costo_unit * cantidad_dec

    # Actualizar stock según tipo de movimiento
    if mov_in.tipo_movimiento in ("entrada_compra", "devolucion"):
        item.stock_actual += cantidad_dec
    elif mov_in.tipo_movimiento in ("salida_consumo", "ajuste_merma"):
        if item.stock_actual < cantidad_dec:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Stock insuficiente para '{item.nombre}'. Disponible: {item.stock_actual} {item.unidad_medida}, Solicitado: {cantidad_dec}",
            )
        item.stock_actual -= cantidad_dec
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tipo de movimiento no válido: '{mov_in.tipo_movimiento}'. Debe ser: entrada_compra, salida_consumo, ajuste_merma o devolucion",
        )

    # Resolver usuario operador
    user_id = await get_fallback_user_id(db, current_user)

    nuevo_movimiento = MovimientoInventario(
        item_id=item.id,
        lote_inventario_id=target_lote_id,
        tipo_movimiento=mov_in.tipo_movimiento,
        cantidad=cantidad_dec,
        costo_unitario=costo_unit,
        usuario_id=user_id,
        motivo=mov_in.motivo,
        sync_status="synced",
    )
    db.add(nuevo_movimiento)
    await db.commit()
    await db.refresh(nuevo_movimiento)

    user_name = None
    if nuevo_movimiento.usuario:
        user_name = f"{nuevo_movimiento.usuario.nombre} {nuevo_movimiento.usuario.apellido}".strip()

    return MovimientoInventarioResponse(
        id=nuevo_movimiento.id,
        item_id=nuevo_movimiento.item_id,
        item_nombre=item.nombre,
        lote_id=nuevo_movimiento.lote_inventario_id,
        tipo_movimiento=nuevo_movimiento.tipo_movimiento,
        cantidad=float(nuevo_movimiento.cantidad),
        costo_unitario=float(costo_unit),
        costo_total=float(costo_total),
        fecha_movimiento=nuevo_movimiento.fecha_movimiento,
        motivo=nuevo_movimiento.motivo,
        usuario_id=nuevo_movimiento.usuario_id,
        usuario_nombre=user_name,
        sync_status=nuevo_movimiento.sync_status,
    )

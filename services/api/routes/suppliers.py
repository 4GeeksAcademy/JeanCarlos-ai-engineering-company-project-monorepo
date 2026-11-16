from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from tinydb import TinyDB
from tinydb.table import Document

from ..database import get_database
from ..core.dependencies import CurrentUser, get_current_user
from ..models import (
    Category,
    Country,
    SupplierCreate,
    SupplierListItem,
    SupplierRateUpdate,
    SupplierResponse,
    SupplierStatus,
    SupplierStatusUpdate,
)
from ..services.cache import response_cache

router = APIRouter(
    prefix="/suppliers", tags=["suppliers"], dependencies=[Depends(get_current_user)]
)
Database = Annotated[TinyDB, Depends(get_database)]
SupplierId = Annotated[int, Path(gt=0)]


def find_supplier(database: TinyDB, supplier_id: int) -> Document:
    supplier = database.get(doc_id=supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return supplier


def supplier_response(supplier: Document) -> SupplierResponse:
    return SupplierResponse.model_validate({**supplier, "id": supplier.doc_id})


@router.get("", response_model=list[SupplierListItem])
def list_suppliers(
    database: Database,
    user: CurrentUser,
    country: Country | None = None,
    category: Category | None = None,
):
    cache_key = (user.id, country.value if country else None, category.value if category else None)
    hit, cached, version = response_cache.get("suppliers", cache_key)
    if hit:
        return cached
    result = [
        SupplierListItem.model_validate({**supplier, "id": supplier.doc_id})
        for supplier in database.all()
        if (country is None or supplier["country"] == country.value)
        and (category is None or category.value in supplier["categories"])
    ]
    response_cache.set("suppliers", cache_key, result, ttl_seconds=30, expected_version=version)
    return result


@router.post(
    "", response_model=SupplierResponse, status_code=status.HTTP_201_CREATED
)
def create_supplier(payload: SupplierCreate, database: Database):
    data = payload.model_dump(mode="json")
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    supplier_id = database.insert(data)
    response_cache.invalidate("suppliers")
    return supplier_response(find_supplier(database, supplier_id))


@router.get("/{supplier_id}", response_model=SupplierResponse)
def get_supplier(supplier_id: SupplierId, database: Database):
    return supplier_response(find_supplier(database, supplier_id))


@router.put("/{supplier_id}", response_model=SupplierResponse)
def replace_supplier(
    supplier_id: SupplierId, payload: SupplierCreate, database: Database
):
    existing = find_supplier(database, supplier_id)
    data = payload.model_dump(mode="json")
    data["updated_at"] = (
        datetime.now(timezone.utc).isoformat()
        if payload.monthly_rate != existing["monthly_rate"]
        else existing["updated_at"]
    )
    database.update(data, doc_ids=[supplier_id])
    response_cache.invalidate("suppliers")
    return supplier_response(find_supplier(database, supplier_id))


@router.patch("/{supplier_id}/rate", response_model=SupplierResponse)
def update_rate(
    supplier_id: SupplierId, payload: SupplierRateUpdate, database: Database
):
    find_supplier(database, supplier_id)
    database.update(
        {
            "monthly_rate": payload.monthly_rate,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        doc_ids=[supplier_id],
    )
    response_cache.invalidate("suppliers")
    return supplier_response(find_supplier(database, supplier_id))


@router.patch("/{supplier_id}/status", response_model=SupplierResponse)
def update_status(
    supplier_id: SupplierId, payload: SupplierStatusUpdate, database: Database
):
    find_supplier(database, supplier_id)
    database.update(payload.model_dump(mode="json"), doc_ids=[supplier_id])
    response_cache.invalidate("suppliers")
    return supplier_response(find_supplier(database, supplier_id))


@router.delete("/{supplier_id}", response_model=SupplierResponse)
def delete_supplier(supplier_id: SupplierId, database: Database):
    return update_status(
        supplier_id, SupplierStatusUpdate(status=SupplierStatus.suspended), database
    )

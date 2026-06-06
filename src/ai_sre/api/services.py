"""Subject-service routes.

Implements spec 0004:

    * ``POST /v1/services``         — register the tenant's service.
    * ``GET  /v1/services/{id}``    — read a single service.

Out-of-scope endpoints (topology + metric catalog) ship in spec 0005 and
are kept here as stubs that 501 so the OpenAPI surface stays stable.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ai_sre.api.deps import (
    TenantContext,
    current_tenant,
    get_service_service,
)
from ai_sre.core.service.service import ServiceService
from ai_sre.exceptions import ServiceAlreadyExists
from ai_sre.schemas.service import ServiceCreateRequest, ServiceResponse

router = APIRouter()


def _service_to_response(
    row: Any, *, warnings: list[str] | None = None
) -> ServiceResponse:
    """Build the API response from a :class:`Service` row.

    Pulls ``validation_pending`` out of ``slo_config`` (we stash it there
    to avoid a dedicated column for a single bit; see spec 0004 design).
    """
    slo = row.slo_config or {}
    return ServiceResponse(
        id=row.id,
        name=row.name,
        label_selector=row.label_selector,
        ownership=row.ownership,
        validation_pending=bool(slo.get("validation_pending", False)),
        warnings=warnings or [],
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post(
    "/services",
    status_code=status.HTTP_201_CREATED,
    response_model=ServiceResponse,
    summary="Register the tenant's subject service (one per tenant in MVP).",
)
async def register_service(
    body: ServiceCreateRequest,
    service: ServiceService = Depends(get_service_service),
) -> ServiceResponse:
    """Register the subject service.

    Validates ``label_selector`` against the connected Prometheus before
    persisting. Behaviour:

        * No Prometheus integration → ``validation_pending=True`` in the
          response; no warning.
        * Prometheus present but the validation call failed → registered
          with ``validation_pending=True`` and a warning describing the
          failure.
        * Selector matched 0 series → registered with a warning.
        * Otherwise → registered with empty warnings.

    Returns 409 if the tenant already has a service.
    """
    try:
        result = await service.register(
            name=body.name,
            label_selector=body.label_selector,
            ownership=body.ownership,
        )
    except ServiceAlreadyExists as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": exc.message, "details": exc.details},
        ) from exc
    return _service_to_response(result.service, warnings=result.warnings)


@router.get(
    "/services/{service_id}",
    response_model=ServiceResponse,
    summary="Read a single subject service.",
)
async def get_service(
    service_id: UUID,
    service: ServiceService = Depends(get_service_service),
) -> ServiceResponse:
    """Return the service. 404 if it doesn't belong to the calling tenant."""
    row = await service.get(service_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "service.not_found",
                "message": "Service not found.",
            },
        )
    return _service_to_response(row)


# ---- Out-of-scope routes (spec 0005 implements these) ----


@router.get(
    "/services/{service_id}/topology",
    summary="Read topology (implemented in spec 0005).",
    include_in_schema=False,
)
async def get_topology(
    service_id: str, tenant: TenantContext = Depends(current_tenant)
) -> dict[str, Any]:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "code": "internal",
            "message": "Topology read ships in spec 0005.",
        },
    )


@router.patch(
    "/services/{service_id}/topology",
    summary="Update topology (implemented in spec 0005).",
    include_in_schema=False,
)
async def update_topology(
    service_id: str, tenant: TenantContext = Depends(current_tenant)
) -> dict[str, Any]:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "code": "internal",
            "message": "Topology update ships in spec 0005.",
        },
    )


@router.post(
    "/services/{service_id}/topology/refresh",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Refresh topology (implemented in spec 0005).",
    include_in_schema=False,
)
async def refresh_topology(
    service_id: str, tenant: TenantContext = Depends(current_tenant)
) -> dict[str, Any]:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "code": "internal",
            "message": "Topology refresh ships in spec 0005.",
        },
    )


@router.get(
    "/services/{service_id}/metrics",
    summary="List metric catalog (implemented in spec 0005).",
    include_in_schema=False,
)
async def list_metrics(
    service_id: str, tenant: TenantContext = Depends(current_tenant)
) -> dict[str, Any]:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "code": "internal",
            "message": "Metric catalog ships in spec 0005.",
        },
    )


@router.post(
    "/services/{service_id}/metrics/refresh",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Refresh metric catalog (implemented in spec 0005).",
    include_in_schema=False,
)
async def refresh_metrics(
    service_id: str, tenant: TenantContext = Depends(current_tenant)
) -> dict[str, Any]:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "code": "internal",
            "message": "Metric catalog refresh ships in spec 0005.",
        },
    )

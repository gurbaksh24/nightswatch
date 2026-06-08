"""Subject-service routes.

Implements spec 0004 (registration) and spec 0005 (topology + metric
catalog):

    * ``POST  /v1/services``                       — register the service.
    * ``GET   /v1/services/{id}``                  — read a service.
    * ``GET   /v1/services/{id}/topology``         — read dependency graph.
    * ``PATCH /v1/services/{id}/topology``         — confirm/edit edges.
    * ``POST  /v1/services/{id}/topology/refresh`` — enqueue a refresh.
    * ``GET   /v1/services/{id}/metrics``          — list the metric catalog.
    * ``POST  /v1/services/{id}/metrics/refresh``  — enqueue a refresh.

Discovery itself runs in the worker (``run_metric_catalog_refresh`` /
``run_topology_refresh``); the refresh routes just enqueue. Registration
enqueues an immediate refresh of both so the catalog/topology populate
right after onboarding rather than waiting for the 6h periodic job.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ai_sre.api.deps import (
    get_catalog_service,
    get_job_queue,
    get_service_service,
    get_topology_service,
)
from ai_sre.core.service.catalog_service import CatalogService
from ai_sre.core.service.service import ServiceService
from ai_sre.core.service.topology_service import TopologyService
from ai_sre.exceptions import ServiceAlreadyExists
from ai_sre.models.service import MetricCatalogEntry, ServiceDependency
from ai_sre.queue.base import JobQueue, JobQueueError
from ai_sre.schemas.service import (
    DependencyResponse,
    MetricEntryResponse,
    ServiceCreateRequest,
    ServiceResponse,
    TopologyResponse,
    TopologyUpdateRequest,
)
from ai_sre.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()

_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail={"code": "service.not_found", "message": "Service not found."},
)


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


def _to_topology_response(deps: Sequence[ServiceDependency]) -> TopologyResponse:
    """Group dependency rows into upstream / downstream response lists."""

    def view(dep: ServiceDependency) -> DependencyResponse:
        return DependencyResponse(
            id=dep.id,
            direction=dep.direction,
            name=dep.name,
            confirmed_by_user=dep.confirmed_by_user,
        )

    return TopologyResponse(
        upstream=[view(d) for d in deps if d.direction == "upstream"],
        downstream=[view(d) for d in deps if d.direction == "downstream"],
    )


def _to_metric_response(entry: MetricCatalogEntry) -> MetricEntryResponse:
    return MetricEntryResponse(
        metric_name=entry.metric_name,
        metric_type=entry.metric_type,
        labels=entry.labels,
        unit=entry.unit,
    )


async def _enqueue_or_503(
    job_queue: JobQueue, kind: str, *, tenant_id: UUID, service_id: UUID
) -> str:
    """Enqueue a refresh job, mapping queue failures to 503."""
    try:
        return await job_queue.enqueue(
            kind, {"tenant_id": str(tenant_id), "service_id": str(service_id)}
        )
    except JobQueueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "internal", "message": f"Could not enqueue {kind}: {exc}"},
        ) from exc


async def _enqueue_discovery_best_effort(
    job_queue: JobQueue, *, tenant_id: UUID, service_id: UUID
) -> None:
    """Enqueue catalog + topology refresh on registration.

    Best-effort: a queue failure is logged, not surfaced — registration
    already succeeded, and the 6h periodic refresh is a backstop.
    """
    payload = {"tenant_id": str(tenant_id), "service_id": str(service_id)}
    for kind in ("metric_catalog_refresh", "topology_refresh"):
        try:
            await job_queue.enqueue(kind, payload)
        except JobQueueError as exc:
            logger.warning(
                "service.discovery_enqueue_failed",
                kind=kind,
                service_id=str(service_id),
                error=str(exc),
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
    job_queue: JobQueue = Depends(get_job_queue),
) -> ServiceResponse:
    """Register the subject service.

    Validates ``label_selector`` against the connected Prometheus before
    persisting (see spec 0004), then enqueues an immediate metric-catalog +
    topology refresh (spec 0005). Returns 409 if the tenant already has a
    service.
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

    await _enqueue_discovery_best_effort(
        job_queue,
        tenant_id=result.service.tenant_id,
        service_id=result.service.id,
    )
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
        raise _NOT_FOUND
    return _service_to_response(row)


# ---- Topology (spec 0005) ----


@router.get(
    "/services/{service_id}/topology",
    response_model=TopologyResponse,
    summary="Read the discovered upstream/downstream dependencies.",
)
async def get_topology(
    service_id: UUID,
    topology: TopologyService = Depends(get_topology_service),
) -> TopologyResponse:
    """Return the dependency graph. 404 if the service isn't owned here."""
    deps = await topology.get(service_id)
    if deps is None:
        raise _NOT_FOUND
    return _to_topology_response(deps)


@router.patch(
    "/services/{service_id}/topology",
    response_model=TopologyResponse,
    summary="Confirm or un-confirm discovered dependency edges.",
)
async def update_topology(
    service_id: UUID,
    body: TopologyUpdateRequest,
    topology: TopologyService = Depends(get_topology_service),
) -> TopologyResponse:
    """Apply ``confirmed_by_user`` edits and return the updated topology."""
    edits = [(e.dependency_id, e.confirmed_by_user) for e in body.edits]
    deps = await topology.confirm(service_id, edits)
    if deps is None:
        raise _NOT_FOUND
    return _to_topology_response(deps)


@router.post(
    "/services/{service_id}/topology/refresh",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue an immediate topology refresh.",
)
async def refresh_topology(
    service_id: UUID,
    service: ServiceService = Depends(get_service_service),
    job_queue: JobQueue = Depends(get_job_queue),
) -> dict[str, Any]:
    """Enqueue a topology refresh job. 404 if the service isn't owned here."""
    row = await service.get(service_id)
    if row is None:
        raise _NOT_FOUND
    job_id = await _enqueue_or_503(
        job_queue, "topology_refresh", tenant_id=row.tenant_id, service_id=service_id
    )
    return {"service_id": str(service_id), "job_id": job_id, "status": "queued"}


# ---- Metric catalog (spec 0005) ----


@router.get(
    "/services/{service_id}/metrics",
    response_model=list[MetricEntryResponse],
    summary="List the service's metric catalog (substring filter + paging).",
)
async def list_metrics(
    service_id: UUID,
    catalog: CatalogService = Depends(get_catalog_service),
    name_filter: str | None = Query(default=None, alias="filter"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[MetricEntryResponse]:
    """Return catalog entries. 404 if the service isn't owned here."""
    entries = await catalog.list_metrics(
        service_id, name_filter=name_filter, limit=limit, offset=offset
    )
    if entries is None:
        raise _NOT_FOUND
    return [_to_metric_response(e) for e in entries]


@router.post(
    "/services/{service_id}/metrics/refresh",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue an immediate metric-catalog refresh.",
)
async def refresh_metrics(
    service_id: UUID,
    service: ServiceService = Depends(get_service_service),
    job_queue: JobQueue = Depends(get_job_queue),
) -> dict[str, Any]:
    """Enqueue a metric-catalog refresh job. 404 if not owned here."""
    row = await service.get(service_id)
    if row is None:
        raise _NOT_FOUND
    job_id = await _enqueue_or_503(
        job_queue,
        "metric_catalog_refresh",
        tenant_id=row.tenant_id,
        service_id=service_id,
    )
    return {"service_id": str(service_id), "job_id": job_id, "status": "queued"}

"""Same-origin Forecast Loom workbench; all operations remain advisory."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from lyte.api.routes_forecast import ForecastBody, forecast
from lyte.api.routes_health import source_revision
from lyte.intelligence.forecast_inspection import inspection_envelope

router = APIRouter(prefix="/forecast", tags=["forecast"])
_WORKBENCH = Path(__file__).resolve().parents[1] / "ui" / "forecast.html"


@router.get("")
def capabilities() -> dict[str, object]:
    """Advertise implemented routes, not model quality or production readiness."""
    return {
        "schema": "szl.lyte.forecast-workbench/v1",
        "workbench": "/api/lyte/v2/forecast/workbench",
        "inspection": "/api/lyte/v2/forecast/inspect",
        "forecast": "/api/lyte/v2/forecast",
        "inspection_provider": "baseline",
        "max_context_points": 8192,
        "max_horizon": 1024,
        "execution_authority": "NONE",
        "input_provenance": "CALLER_SUPPLIED_NOT_INDEPENDENTLY_VERIFIED",
    }


@router.get("/workbench", response_class=FileResponse, include_in_schema=False)
def workbench() -> FileResponse:
    return FileResponse(_WORKBENCH, media_type="text/html")


@router.post("/inspect")
def inspect_forecast(body: ForecastBody) -> dict[str, str]:
    """Run the existing Python forecast once and preserve its exact evidence bytes."""
    if body.provider != "baseline":
        raise HTTPException(status_code=422, detail="Workbench supports the baseline only")
    if body.quantiles != [0.1, 0.5, 0.9]:
        raise HTTPException(status_code=422, detail="Workbench requires q10, q50, q90")
    if body.risk is None:
        raise HTTPException(status_code=422, detail="A threshold-risk rule is required")
    revision = source_revision()
    if revision is None:
        raise HTTPException(status_code=503, detail="Exact source identity is unavailable")
    result = forecast(body)
    if source_revision() != revision:
        raise HTTPException(status_code=503, detail="Source identity changed during inspection")
    try:
        return inspection_envelope(body.model_dump(mode="json"), result, revision=revision)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Forecast evidence unavailable") from exc

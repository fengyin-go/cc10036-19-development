"""布局导入接口：导入、核验、订阅日报、投递对比和口径重算定位。

路由挂载在通用资源路由之前：`/api/layout-imports` 不是业务模块 key，
必须先注册，否则会被 `/api/{module}` 动态路径抢先匹配。
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.services import layout_import as service

router = APIRouter(prefix="/api/layout-imports", tags=["布局导入"])


class LayoutImportPayload(BaseModel):
    layout: dict[str, Any] | str | None = Field(
        default=None, description="布局文件内容，支持 JSON/YAML 文本或对象；为空时读取默认示例"
    )
    path: str | None = Field(default=None, description="布局文件路径（相对 backend/）")
    target_domain: str | None = Field(default=None, description="目标业务域，覆盖文件里声明的值")
    created_by: str = "数据工程组"


class ConfirmBindingPayload(BaseModel):
    granularity: str = Field(description="核准后的目标域粒度，如「按小时」「按日」")
    aggregator: str | None = Field(default=None, description="聚合函数，默认取目标指标定义")
    metric_code: str | None = Field(default=None, description="显式指定目标域指标编码")
    verified_by: str = "数据治理组"
    note: str = ""


class SendReportPayload(BaseModel):
    report_date: str | None = None
    trigger: str = "manual"


class CaliberChangePayload(BaseModel):
    metric_code: str
    caliber: str
    aggregator: str | None = None
    granularity: str | None = None
    changed_by: str = "数据治理组"


def _fail(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("")
def list_imports() -> dict[str, Any]:
    return service.list_imports()


@router.post("")
async def import_layout(request: Request) -> dict[str, Any]:
    """导入布局。支持 JSON 请求体、纯文本文件内容，以及 JSON 里的 path 字段。"""
    content_type = (request.headers.get("content-type") or "").lower()
    try:
        if "application/json" in content_type:
            raw = await request.body()
            body = json.loads(raw.decode("utf-8")) if raw.strip() else {}
            payload = LayoutImportPayload(**body)
            return service.import_layout(
                layout=payload.layout,
                path=payload.path,
                target_domain=payload.target_domain,
                created_by=payload.created_by,
            )
        raw = await request.body()
        text = raw.decode("utf-8")
        return service.import_layout(layout=text, created_by="数据工程组")
    except service.LayoutImportError as exc:
        raise _fail(exc) from exc


# ---- 日报投递查询放在 /{import_id} 之前，避免 "deliveries" 被当成导入编号 ----
@router.get("/deliveries")
def list_deliveries(import_id: int | None = None) -> dict[str, Any]:
    return service.list_deliveries(import_id)


@router.get("/deliveries/compare")
def compare_deliveries(left: int, right: int) -> dict[str, Any]:
    try:
        return service.compare_deliveries(left, right)
    except service.LayoutImportError as exc:
        raise _fail(exc) from exc


@router.get("/deliveries/{delivery_id}")
def delivery_detail(delivery_id: int) -> dict[str, Any]:
    try:
        return service.delivery_detail(delivery_id)
    except service.LayoutImportError as exc:
        raise _fail(exc) from exc


@router.get("/deliveries/{delivery_id}/file", response_class=PlainTextResponse)
def delivery_file(delivery_id: int) -> PlainTextResponse:
    try:
        detail = service.delivery_detail(delivery_id)
    except service.LayoutImportError as exc:
        raise _fail(exc) from exc
    # HTTP 头只能放 latin-1，中文文件名用 RFC 5987 的 filename* 传，另留 ASCII 兜底。
    file_name = detail["file_name"]
    quoted = quote(file_name, safe="")
    return PlainTextResponse(
        detail["file_content"],
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"delivery_{delivery_id}.csv\"; "
                f"filename*=UTF-8''{quoted}"
            ),
        },
    )


@router.get("/caliber-recalc")
def recalc_metric(metric_code: str) -> dict[str, Any]:
    try:
        return service.recalc_metric(metric_code)
    except service.LayoutImportError as exc:
        raise _fail(exc) from exc


@router.post("/caliber-changes")
def change_caliber(payload: CaliberChangePayload) -> dict[str, Any]:
    try:
        return service.change_caliber(
            payload.metric_code,
            caliber=payload.caliber,
            aggregator=payload.aggregator,
            granularity=payload.granularity,
            changed_by=payload.changed_by,
        )
    except service.LayoutImportError as exc:
        raise _fail(exc) from exc


@router.get("/{import_id}")
def import_detail(import_id: int) -> dict[str, Any]:
    try:
        return service.detail(import_id)
    except service.LayoutImportError as exc:
        raise _fail(exc) from exc


@router.post("/{import_id}/bindings/{binding_id}/confirm")
def confirm_binding(import_id: int, binding_id: str, payload: ConfirmBindingPayload) -> dict[str, Any]:
    try:
        return service.confirm_binding(
            import_id,
            binding_id,
            granularity=payload.granularity,
            aggregator=payload.aggregator,
            metric_code=payload.metric_code,
            verified_by=payload.verified_by,
            note=payload.note,
        )
    except service.LayoutImportError as exc:
        raise _fail(exc) from exc


@router.post("/{import_id}/subscriptions/{subscription_id}/send")
def send_subscription_report(
    import_id: int, subscription_id: int, payload: SendReportPayload | None = None,
) -> dict[str, Any]:
    try:
        subscription = service.registry.find_subscription(subscription_id)
        if subscription is None or int(subscription["import_id"]) != int(import_id):
            raise service.LayoutImportError(
                f"订阅 {subscription_id} 不属于布局导入 {import_id}"
            )
        return service.send_daily_report(
            subscription_id,
            report_date=(payload.report_date if payload else None),
            trigger=(payload.trigger if payload else "manual"),
        )
    except service.LayoutImportError as exc:
        raise _fail(exc) from exc


@router.post("/{import_id}/send")
def send_import_report(import_id: int, payload: SendReportPayload | None = None) -> dict[str, Any]:
    """按导入批次默认订阅发送一次日报。"""
    try:
        record = service.registry.find_import(import_id)
        if record is None:
            raise service.LayoutImportError(f"布局导入 {import_id} 不存在")
        subscription_id = record.get("subscription_id")
        if subscription_id is None:
            raise service.LayoutImportError(f"布局导入 {import_id} 还没有订阅")
        return service.send_daily_report(
            int(subscription_id),
            report_date=(payload.report_date if payload else None),
            trigger=(payload.trigger if payload else "manual"),
        )
    except service.LayoutImportError as exc:
        raise _fail(exc) from exc

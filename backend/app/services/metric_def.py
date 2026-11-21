"""指标定义领域服务：指标聚合计算、口径统一、跨模块指标消费。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store
from app.analytics_engine import compute_metric, AGGREGATORS


class MetricDefService(BaseModuleService):
    MODULE = "metric_def"
    ENTITY = "指标"
    OPERATION_TERM = "聚合"
    LIST_FIELDS = ["指标编码", "指标名称", "业务域", "计算口径", "聚合函数", "数据粒度", "是否可累加"]
    REQUIRED_FIELDS = ["指标编码", "指标名称", "业务域"]
    STATUS_ORDER = ["草稿", "已审核", "已发布", "已停用"]
    ACTION_RULES = {"提交审核": "已审核", "发布指标": "已发布", "停用指标": "已停用"}
    NEGATIVE_ACTIONS = ["停用指标"]

    # ---- 业务方法 ----

    def calc_metric_value(self, metric_code: str, raw_values: list[float | int]) -> dict[str, Any]:
        """根据指标编码查找口径，执行聚合计算。"""
        rows = store.rows(self.MODULE)
        metric_def = next(
            (row for row in rows if row.get("指标编码") == metric_code and row.get("status") == "已发布"),
            None,
        )
        if metric_def is None:
            return {"ok": False, "message": f"指标 {metric_code} 未找到或未发布"}
        aggregator = str(metric_def.get("聚合函数", "SUM"))
        if aggregator not in AGGREGATORS:
            return {"ok": False, "message": f"不支持的聚合函数：{aggregator}"}
        result = compute_metric(raw_values, aggregator)
        return {
            "ok": True,
            "metric_code": metric_code,
            "metric_name": str(metric_def.get("指标名称", "")),
            "aggregator": aggregator,
            "result": result,
            "value_count": len(raw_values),
        }

    def get_published_metrics_by_domain(self, domain: str) -> list[dict[str, Any]]:
        """获取某业务域下所有已发布指标（用于立方构建消费）。"""
        rows = store.rows(self.MODULE)
        return [
            row for row in rows
            if row.get("业务域") == domain and row.get("status") == "已发布"
        ]

    def resolve_metric_caliber(self, metric_code: str) -> dict[str, Any] | None:
        """解析指标口径（用于语义层映射和跨域布局导入核验）。"""
        rows = store.rows(self.MODULE)
        for row in rows:
            if row.get("指标编码") == metric_code:
                return {
                    "code": row.get("指标编码"),
                    "name": row.get("指标名称"),
                    "domain": row.get("业务域"),
                    "caliber": row.get("计算口径"),
                    "aggregator": row.get("聚合函数"),
                    "granularity": row.get("数据粒度"),
                    "additive": row.get("是否可累加"),
                    "caliber_version": int(row.get("_caliber_version", 1)),
                }
        return None

    def find_metric_by_name(self, name: str, domain: str) -> dict[str, Any] | None:
        """按名称在指定业务域内查找指标，只返回唯一命中；同名多条返回 None 由调用方处理。"""
        matches = [
            row for row in store.rows(self.MODULE)
            if str(row.get("指标名称", "")).strip() == str(name).strip()
            and str(row.get("业务域", "")).strip() == str(domain).strip()
        ]
        return matches[0] if len(matches) == 1 else None

    def list_domain_metrics(self, domain: str) -> list[dict[str, Any]]:
        return [
            row for row in store.rows(self.MODULE)
            if str(row.get("业务域", "")).strip() == str(domain).strip()
        ]

    def update_caliber(
        self, metric_code: str, *,
        caliber: str,
        aggregator: str | None = None,
        granularity: str | None = None,
        changed_by: str = "",
    ) -> dict[str, Any] | None:
        """变更指标口径并递增版本号；返回变更前后快照供订阅端定位历史绑定。"""
        for row in store.rows(self.MODULE):
            if row.get("指标编码") != metric_code:
                continue
            before = {
                "caliber": row.get("计算口径"),
                "aggregator": row.get("聚合函数"),
                "granularity": row.get("数据粒度"),
                "caliber_version": int(row.get("_caliber_version", 1)),
            }
            row["计算口径"] = caliber
            if aggregator:
                row["聚合函数"] = aggregator
            if granularity:
                row["数据粒度"] = granularity
            row["_caliber_version"] = before["caliber_version"] + 1
            return {
                "ok": True,
                "metric_code": metric_code,
                "metric_name": row.get("指标名称"),
                "domain": row.get("业务域"),
                "before": before,
                "after": {
                    "caliber": row.get("计算口径"),
                    "aggregator": row.get("聚合函数"),
                    "granularity": row.get("数据粒度"),
                    "caliber_version": row.get("_caliber_version"),
                },
                "changed_by": changed_by,
            }
        return None

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        base["artifact"] = "指标数据集"
        base["published_metrics"] = sum(
            1 for row in store.rows(self.MODULE) if row.get("status") == "已发布"
        )
        return base
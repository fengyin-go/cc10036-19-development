"""布局导入的进程内仓库：导入批次、指标绑定、订阅、日报投递与核验台账。

仓库与 `app.store.Store`（18个业务模块的示例数据）分开存放，避免把导入、
订阅、投递这类运行期对象混进业务模块列表和运营概览。
"""
from __future__ import annotations

from typing import Any


def _seed_verifications() -> list[dict[str, Any]]:
    """目标域已有的核验台账。

    活跃设备数此前已经按「按日 / DISTINCT_COUNT」核验过，因此导入同粒度、
    同聚合函数的绑定时可以直接复用；销售额没有台账记录，必须人工核准。
    """
    return [
        {
            "target_metric_code": "ACTIVE_DEVICE",
            "target_domain": "营收分析",
            "granularity": "按日",
            "aggregator": "DISTINCT_COUNT",
            "caliber_version": 1,
            "verified_by": "数据治理组",
            "verified_at": "2026-11-18",
            "note": "活跃设备数已与用户增长域对齐设备去重口径",
        },
    ]


class LayoutRegistry:
    """保存所有布局导入、绑定、订阅和投递记录，并提供自增编号。"""

    def __init__(self) -> None:
        self.imports: list[dict[str, Any]] = []
        self.bindings: list[dict[str, Any]] = []
        self.subscriptions: list[dict[str, Any]] = []
        self.deliveries: list[dict[str, Any]] = []
        self.verifications: list[dict[str, Any]] = _seed_verifications()
        self.caliber_events: list[dict[str, Any]] = []
        self._seq: dict[str, int] = {
            "import": 0,
            "binding": 0,
            "subscription": 0,
            "delivery": 0,
        }

    def next_id(self, kind: str) -> int:
        self._seq[kind] = self._seq.get(kind, 0) + 1
        return self._seq[kind]

    def next_binding_code(self) -> str:
        return f"BD-{self.next_id('binding'):04d}"

    def find_import(self, import_id: int) -> dict[str, Any] | None:
        return next(
            (item for item in self.imports if int(item["id"]) == import_id), None
        )

    def find_binding(self, binding_id: str) -> dict[str, Any] | None:
        return next(
            (item for item in self.bindings if item["binding_id"] == binding_id), None
        )

    def find_subscription(self, subscription_id: int) -> dict[str, Any] | None:
        return next(
            (item for item in self.subscriptions if int(item["id"]) == subscription_id),
            None,
        )

    def find_delivery(self, delivery_id: int) -> dict[str, Any] | None:
        return next(
            (item for item in self.deliveries if int(item["id"]) == delivery_id), None
        )

    def binding_for_import_card(self, import_id: int, card_id: str) -> dict[str, Any] | None:
        return next(
            (
                item for item in self.bindings
                if int(item["import_id"]) == import_id and item["card_id"] == card_id
            ),
            None,
        )

    def find_verification(
        self, metric_code: str, granularity: str, aggregator: str,
    ) -> dict[str, Any] | None:
        return next(
            (
                item for item in self.verifications
                if item["target_metric_code"] == metric_code
                and item["granularity"] == granularity
                and str(item["aggregator"]).upper() == str(aggregator).upper()
            ),
            None,
        )

    def record_verification(self, entry: dict[str, Any]) -> None:
        existing = self.find_verification(
            entry["target_metric_code"], entry["granularity"], entry["aggregator"],
        )
        if existing:
            existing.update(entry)
            return
        self.verifications.append(dict(entry))

    def bindings_using_metric(self, metric_code: str) -> list[dict[str, Any]]:
        return [item for item in self.bindings if item["metric_code"] == metric_code]

    def deliveries_using_metric(self, metric_code: str) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        for delivery in self.deliveries:
            rows = {row["card_id"]: row for row in delivery.get("rows", [])}
            for snapshot in delivery.get("binding_snapshot", []):
                if snapshot.get("metric_code") != metric_code:
                    continue
                row = rows.get(snapshot["card_id"], {})
                hits.append({
                    "delivery": delivery,
                    "snapshot": snapshot,
                    "value": row.get("value"),
                    "publish_state": row.get("publish_state"),
                })
        return hits

    def deliveries_using_binding(self, binding_id: str) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        for delivery in self.deliveries:
            rows = {row["card_id"]: row for row in delivery.get("rows", [])}
            for snapshot in delivery.get("binding_snapshot", []):
                if snapshot.get("binding_id") != binding_id:
                    continue
                row = rows.get(snapshot["card_id"], {})
                hits.append({
                    "delivery": delivery,
                    "snapshot": snapshot,
                    "value": row.get("value"),
                    "publish_state": row.get("publish_state"),
                })
        return hits


registry = LayoutRegistry()

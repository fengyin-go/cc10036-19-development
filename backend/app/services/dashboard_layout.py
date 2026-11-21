"""看板布局领域服务：看板配置、布局管理、数据消费。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class DashboardLayoutService(BaseModuleService):
    MODULE = "dashboard_layout"
    ENTITY = "看板配置"
    OPERATION_TERM = "聚合"
    LIST_FIELDS = ["看板名称", "所属业务域", "图表布局", "数据刷新间隔", "是否公开", "创建人", "上次修改"]
    REQUIRED_FIELDS = ["看板名称", "所属业务域", "图表布局"]
    STATUS_ORDER = ["草稿", "已发布", "已下线"]
    ACTION_RULES = {"发布看板": "已发布", "下线看板": "已下线"}
    NEGATIVE_ACTIONS = ["下线看板"]

    # ---- 业务方法 ----

    def get_published_dashboards(self) -> list[dict[str, Any]]:
        """获取所有已发布看板（供报表订阅和导出消费）。"""
        return [
            row for row in store.rows(self.MODULE)
            if row.get("status") == "已发布"
        ]

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = super().get_entry(entry_id)
        if entry is None:
            return None
        return self._with_bindings(entry)

    def _with_bindings(self, entry: dict[str, Any]) -> dict[str, Any]:
        """布局详情同步展示导入时实际采用的绑定，便于和订阅日报对照。"""
        import_id = entry.get("_import_id")
        if not import_id:
            return entry
        from app.services import layout_import

        return {
            **entry,
            "来源布局导入": import_id,
            "指标绑定": layout_import.binding_snapshot_for_import(int(import_id)),
        }

    def parse_layout(self, dashboard_id: int) -> dict[str, Any]:
        """解析看板布局配置，拆分为独立的图表卡片。"""
        dashboard = store.find(self.MODULE, dashboard_id)
        if dashboard is None:
            return {"ok": False, "message": f"看板 {dashboard_id} 不存在"}
        layout_raw = str(dashboard.get("图表布局", ""))
        # 格式: type:title:w:h:x:y;type:title:w:h:x:y
        cards: list[dict[str, Any]] = []
        for card_str in layout_raw.split(";"):
            parts = card_str.strip().split(":")
            if len(parts) >= 2:
                cards.append({
                    "type": parts[0].strip(),
                    "title": parts[1].strip(),
                    "width": int(parts[2]) if len(parts) > 2 else 1,
                    "height": int(parts[3]) if len(parts) > 3 else 1,
                    "x": int(parts[4]) if len(parts) > 4 else 0,
                    "y": int(parts[5]) if len(parts) > 5 else 0,
                })
        result = {
            "ok": True,
            "dashboard_name": str(dashboard.get("看板名称", "")),
            "domain": str(dashboard.get("所属业务域", "")),
            "refresh_interval": dashboard.get("数据刷新间隔"),
            "cards": cards,
            "card_count": len(cards),
        }
        import_id = dashboard.get("_import_id")
        if import_id:
            from app.services import layout_import

            result["import_id"] = int(import_id)
            result["bindings"] = layout_import.binding_snapshot_for_import(int(import_id))
        return result

    def get_dashboards_by_domain(self, domain: str) -> list[dict[str, Any]]:
        """获取某业务域下所有看板。"""
        return [
            row for row in store.rows(self.MODULE)
            if row.get("所属业务域") == domain
        ]

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        rows = store.rows(self.MODULE)
        base["artifact"] = "看板布局数据集"
        base["published_count"] = sum(1 for row in rows if row.get("status") == "已发布")
        base["public_count"] = sum(1 for row in rows if row.get("是否公开") == "是")
        return base
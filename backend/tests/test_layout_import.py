"""布局导入核心业务回归：导入核验、日报发布范围、口径变更定位。

运行：cd backend && .venv/bin/python -m unittest discover -s tests -v
这些用例直接调用领域服务，不依赖 HTTP 客户端。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import layout_import as service  # noqa: E402
from app.store import store  # noqa: E402


def _reset_registry() -> None:
    from app.layout_store import LayoutRegistry, registry

    fresh = LayoutRegistry()
    registry.imports = fresh.imports
    registry.bindings = fresh.bindings
    registry.subscriptions = fresh.subscriptions
    registry.deliveries = fresh.deliveries
    registry.verifications = fresh.verifications
    registry.caliber_events = fresh.caliber_events
    registry._seq = fresh._seq

    # 指标定义里的口径版本可能被上一个用例改过，这里恢复种子值
    for row in store.rows("metric_def"):
        row["_caliber_version"] = 1
    for row in store.rows("metric_def"):
        if row.get("指标编码") == "SALES_AMT":
            row["计算口径"] = "按小时汇总已完成订单实付金额"
            row["聚合函数"] = "SUM"
            row["数据粒度"] = "按小时"


class LayoutImportTestCase(unittest.TestCase):
    def setUp(self) -> None:
        _reset_registry()

    def _import_sample(self) -> dict:
        return service.import_layout(target_domain=None)

    def test_same_name_does_not_confirm(self) -> None:
        result = self._import_sample()
        sales = next(card for card in result["cards"] if card["card_id"] == "sales")
        binding = sales["binding"]

        self.assertEqual(binding["target_metric_code"], "SALES_AMT")
        self.assertEqual(binding["status"], "待核")
        self.assertTrue(binding["name_only_match"])
        codes = {item["code"] for item in binding["pending_items"]}
        self.assertIn("granularity_mismatch", codes)
        self.assertIn("unverified_in_target", codes)
        # 目标域按小时，导入按日
        self.assertEqual(binding["effective_binding"]["granularity"], "按小时")
        self.assertEqual(binding["effective_binding"]["aggregator"], "SUM")

    def test_preverified_metric_reuses_target_ledger(self) -> None:
        result = self._import_sample()
        device = next(card for card in result["cards"] if card["card_id"] == "active_device")
        binding = device["binding"]

        self.assertEqual(binding["status"], "已确认")
        self.assertEqual(binding["confirmed_granularity"], "按日")
        self.assertEqual(binding["confirmed_aggregator"], "DISTINCT_COUNT")
        self.assertEqual(binding["confirmation_basis"], "目标域核验台账")
        self.assertEqual(binding["pending_items"], [])

    def test_file_claim_is_ignored(self) -> None:
        document = json.loads(service.SAMPLE_PATH.read_text(encoding="utf-8"))
        document["cards"][0]["metric"]["confirmed"] = True
        document["cards"][0]["verified"] = "是"
        result = service.import_layout(layout=document)
        sales = next(card for card in result["cards"] if card["card_id"] == "sales")

        self.assertEqual(sales["binding"]["status"], "待核")
        self.assertTrue(sales["import_claims"])
        self.assertTrue(sales["binding"]["pending_items"])

    def test_report_withholds_unverified_values(self) -> None:
        result = self._import_sample()
        delivery = service.send_daily_report(result["subscription"]["id"], report_date="2026-11-20")

        rows = {row["card_id"]: row for row in delivery["rows"]}
        self.assertIsNone(rows["sales"]["value"])
        self.assertEqual(rows["sales"]["publish_state"], "待核不发布")
        self.assertEqual(rows["active_device"]["value"], 10.0)
        self.assertEqual(rows["active_device"]["publish_state"], "已发布")
        self.assertEqual(delivery["published_count"], 1)
        self.assertEqual(delivery["withheld_count"], 1)

        # 未发布的值不能出现在导出的文件里
        self.assertIn("待核不发布", delivery["file_content"])
        sales_line = next(
            line for line in delivery["file_content"].splitlines() if ",sales," in line
        )
        fields = sales_line.split(",")
        value_index = delivery["file_content"].splitlines()[0].split(",").index("value")
        self.assertEqual(fields[value_index], "")

    def test_confirm_granularity_then_publish(self) -> None:
        result = self._import_sample()
        import_id = result["id"]
        sales = next(card for card in result["cards"] if card["card_id"] == "sales")
        binding_id = sales["binding"]["binding_id"]

        with self.assertRaises(service.LayoutImportError):
            service.confirm_binding(import_id, binding_id, granularity="按日")

        confirmed = service.confirm_binding(import_id, binding_id, granularity="按小时")
        self.assertEqual(confirmed["binding"]["status"], "已确认")
        self.assertEqual(confirmed["binding"]["confirmed_granularity"], "按小时")
        self.assertEqual(confirmed["binding"]["confirmed_aggregator"], "SUM")

        delivery = service.send_daily_report(
            result["subscription"]["id"], report_date="2026-11-20"
        )
        rows = {row["card_id"]: row for row in delivery["rows"]}
        self.assertEqual(rows["sales"]["value"], 3000.0)
        self.assertEqual(rows["sales"]["publish_state"], "已发布")
        self.assertEqual(delivery["withheld_count"], 0)
        self.assertEqual(delivery["pending_count"], 0)

    def test_compare_reports(self) -> None:
        result = self._import_sample()
        subscription_id = result["subscription"]["id"]
        first = service.send_daily_report(subscription_id, report_date="2026-11-20")

        sales = next(card for card in result["cards"] if card["card_id"] == "sales")
        service.confirm_binding(result["id"], sales["binding"]["binding_id"], granularity="按小时")
        second = service.send_daily_report(subscription_id, report_date="2026-11-20")

        comparison = service.compare_deliveries(first["id"], second["id"])
        cards = {row["card_id"]: row for row in comparison["cards"]}
        self.assertIsNone(cards["sales"]["left"]["value"])
        self.assertEqual(cards["sales"]["right"]["value"], 3000.0)
        self.assertIn("unverified_in_target", cards["sales"]["pending_resolved"])
        self.assertEqual(cards["active_device"]["left"]["value"], 10.0)
        self.assertEqual(cards["active_device"]["right"]["value"], 10.0)
        self.assertEqual(cards["active_device"]["pending_resolved"], [])

    def test_caliber_change_marks_binding_and_locates_recalc(self) -> None:
        result = self._import_sample()
        sales = next(card for card in result["cards"] if card["card_id"] == "sales")
        binding_id = sales["binding"]["binding_id"]
        service.confirm_binding(result["id"], binding_id, granularity="按小时")
        delivery = service.send_daily_report(
            result["subscription"]["id"], report_date="2026-11-20"
        )
        self.assertEqual(delivery["published_count"], 2)

        change = service.change_caliber(
            "SALES_AMT", caliber="按小时汇总已完成订单实付金额（含退款冲抵）",
        )
        self.assertEqual(len(change["affected_bindings"]), 1)
        self.assertEqual(change["affected_bindings"][0]["binding_id"], binding_id)

        recalc = service.recalc_metric("SALES_AMT")
        target = next(item for item in recalc["bindings"] if item["binding_id"] == binding_id)
        self.assertEqual(target["status"], "待复核")
        self.assertTrue(target["stale"])
        self.assertEqual(target["metric_code"], "SALES_AMT")
        self.assertEqual(target["publish_state"], "待复核不发布")
        self.assertEqual(
            [item["delivery_id"] for item in target["used_by_deliveries"]], [delivery["id"]]
        )

        # 口径变更后重新发送，不能继续发布旧口径数值
        after = service.send_daily_report(
            result["subscription"]["id"], report_date="2026-11-21"
        )
        rows = {row["card_id"]: row for row in after["rows"]}
        self.assertIsNone(rows["sales"]["value"])
        self.assertEqual(rows["sales"]["publish_state"], "待核不发布")
        self.assertEqual(rows["active_device"]["value"], 10.0)

    def test_paused_subscription_cannot_send(self) -> None:
        result = self._import_sample()
        subscription_id = result["subscription"]["id"]
        subscription = service.registry.find_subscription(subscription_id)
        subscription["status"] = "已暂停"
        with self.assertRaises(service.LayoutImportError):
            service.send_daily_report(subscription_id)


if __name__ == "__main__":
    unittest.main()

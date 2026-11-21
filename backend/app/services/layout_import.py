"""布局导入领域服务：解析带指标绑定的看板布局并做跨域核验。

关键约束
--------
* 导入文件里的卡片只描述「来源口径」，同名指标不因为名字相同就算已确认；
  唯一认可的确认依据是目标域已有的核验台账（粒度 + 聚合函数一致）。
* 订阅日报只发布已核验的数值，未核验的卡片保留行但数值留空，并带出待核项。
* 每条日报都保存实际采用的绑定快照（binding_id / 粒度 / 聚合函数 / 口径版本），
  口径变更后的重算结果按 binding_id 反查，不重新按指标名猜测。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from app.analytics_engine import compute_metric
from app.layout_store import registry
from app.services.metric_def import MetricDefService
from app.store import store

metric_service = MetricDefService()

BACKEND_DIR = Path(__file__).resolve().parents[2]
SAMPLE_PATH = BACKEND_DIR / "samples" / "revenue_layout.json"
REPORT_DIR = BACKEND_DIR / "runtime" / "layout_reports"

GRANULARITY_ORDER = ("小时", "日", "周", "月", "季", "年")
DEFAULT_SUBSCRIPTION_NAME_SUFFIX = "日报"

# 文件里可能自带的「已确认」声明。导入内容不可信，一律忽略，只作为审计记录保留。
FILE_CLAIM_KEYS = ("confirmed", "verified", "verification_status", "已确认", "核验状态")


class LayoutImportError(ValueError):
    """布局导入 / 核验 / 投递失败，路由层转换为 400。"""


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def _first(raw: dict[str, Any], keys: tuple[str, ...], default: Any = None) -> Any:
    for key in keys:
        if key in raw and raw[key] not in (None, ""):
            return raw[key]
    return default


def _granularity_units(value: Any) -> list[str]:
    text = str(value or "")
    return [unit for unit in GRANULARITY_ORDER if unit in text]


def _granularity_compatible(imported: Any, target: Any) -> bool:
    """来源粒度必须落在目标指标声明的粒度范围内。"""
    imported_units = set(_granularity_units(imported))
    target_units = set(_granularity_units(target))
    if not imported_units or not target_units:
        return str(imported or "").strip() == str(target or "").strip()
    return imported_units <= target_units


def _pick_granularity(value: Any) -> str:
    """把「按日/按小时」这类复合粒度收敛成单个粒度，统一成「按X」的写法。"""
    units = _granularity_units(value)
    return f"按{units[0]}" if units else ""


def _same_text(left: Any, right: Any) -> bool:
    return str(left or "").strip() == str(right or "").strip()


def _fmt_number(value: float | int | None) -> str:
    if value is None:
        return ""
    number = float(value)
    if math.isfinite(number) and number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w一-鿿-]+", "_", name).strip("_") or "dashboard"


def _pending_item(code: str, field: str, message: str, imported: Any, target: Any) -> dict[str, Any]:
    return {
        "code": code,
        "field": field,
        "message": message,
        "imported": imported,
        "target": target,
    }


# ---------------------------------------------------------------------------
# 解析布局文件
# ---------------------------------------------------------------------------


def load_layout_document(
    layout: dict[str, Any] | str | None = None,
    path: str | None = None,
) -> tuple[dict[str, Any], str]:
    """读取布局文件，返回 (文档, 来源说明)。支持 JSON / YAML 文本和本地示例路径。"""
    source = ""
    if isinstance(layout, dict):
        return layout, "inline"
    if path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = BACKEND_DIR / path
        if not candidate.exists():
            raise LayoutImportError(f"布局文件不存在：{path}")
        text = candidate.read_text(encoding="utf-8")
        source = str(candidate)
    elif isinstance(layout, str) and layout.strip():
        text = layout
        source = "inline"
    else:
        if not SAMPLE_PATH.exists():
            raise LayoutImportError("未提供布局内容，默认示例布局文件也不存在")
        text = SAMPLE_PATH.read_text(encoding="utf-8")
        source = str(SAMPLE_PATH)

    try:
        document = json.loads(text)
        return document, source
    except json.JSONDecodeError:
        pass
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise LayoutImportError(f"布局文件无法解析：{exc}") from exc
    if not isinstance(loaded, dict):
        raise LayoutImportError("布局文件顶层必须是对象")
    return loaded, source


def _parse_card(raw: dict[str, Any], index: int, source_domain: str) -> dict[str, Any]:
    metric_raw = raw.get("metric") or raw.get("指标") or {}
    if not isinstance(metric_raw, dict):
        metric_raw = {"name": str(metric_raw)}

    name = _first(metric_raw, ("name", "指标名称", "metric_name")) or _first(
        raw, ("metric_name", "指标名称", "title", "标题", "name")
    )
    code = _first(metric_raw, ("code", "指标编码", "metric_code")) or _first(
        raw, ("metric_code", "指标编码")
    )
    domain = _first(metric_raw, ("domain", "业务域")) or source_domain
    caliber = _first(metric_raw, ("caliber", "计算口径")) or _first(raw, ("caliber", "计算口径"))
    aggregator = _first(metric_raw, ("aggregator", "聚合函数")) or _first(
        raw, ("aggregator", "聚合函数")
    )
    granularity = _first(metric_raw, ("granularity", "数据粒度")) or _first(
        raw, ("granularity", "数据粒度")
    )

    data_raw = raw.get("data") or raw.get("数据") or {}
    if not isinstance(data_raw, dict):
        data_raw = {"rows": data_raw}
    rows = data_raw.get("rows") or raw.get("rows") or raw.get("示例数据") or []
    if not isinstance(rows, list):
        rows = []
    time_field = _first(data_raw, ("time_field", "时间字段")) or _first(raw, ("time_field",))
    value_field = _first(data_raw, ("value_field", "数值字段")) or _first(raw, ("value_field",))
    if not value_field and rows and isinstance(rows[0], dict):
        value_field = next(
            (key for key in rows[0] if key not in (time_field,)), None
        )

    layout = raw.get("layout") or raw.get("位置") or ""
    width = _first(raw, ("w", "width", "宽"), 1)
    height = _first(raw, ("h", "height", "高"), 1)
    x = _first(raw, ("x",), 0)
    y = _first(raw, ("y",), 0)
    if isinstance(layout, str) and ":" in layout:
        parts = layout.split(":")
        width = parts[0] if len(parts) > 0 else width
        height = parts[1] if len(parts) > 1 else height
        x = parts[2] if len(parts) > 2 else x
        y = parts[3] if len(parts) > 3 else y

    file_claims = {
        key: raw[key] if key in raw else metric_raw.get(key)
        for key in FILE_CLAIM_KEYS
        if key in raw or key in metric_raw
    }

    return {
        "card_id": str(_first(raw, ("card_id", "id", "卡片ID"), f"card-{index + 1}")),
        "title": str(_first(raw, ("title", "标题", "名称"), name or f"卡片{index + 1}")),
        "type": str(_first(raw, ("type", "图表类型"), "number")),
        "layout": {
            "w": _as_int(width, 1),
            "h": _as_int(height, 1),
            "x": _as_int(x, 0),
            "y": _as_int(y, 0),
        },
        "metric": {
            "name": str(name or "").strip(),
            "code": str(code or "").strip(),
            "domain": str(domain or source_domain).strip(),
            "caliber": str(caliber or "").strip(),
            "aggregator": str(aggregator or "").strip().upper(),
            "granularity": str(granularity or "").strip(),
        },
        "data": {
            "time_field": str(time_field or "").strip(),
            "value_field": str(value_field or "").strip(),
            "unit": str(_first(data_raw, ("unit", "单位")) or _first(raw, ("unit", "单位"), "")).strip(),
            "rows": rows,
        },
        "import_claims": file_claims,
    }


def _as_int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _resolve_source_metric(card_metric: dict[str, Any], source_domain: str) -> dict[str, Any]:
    """按来源域解析导入卡片背后的指标，只用于补全口径描述，不代表目标域确认。"""
    if card_metric.get("code"):
        resolved = metric_service.resolve_metric_caliber(card_metric["code"])
        if resolved:
            return resolved
    if card_metric.get("name"):
        try:
            found = metric_service.find_metric_by_name(card_metric["name"], source_domain)
        except Exception:  # pragma: no cover - 防御性兜底
            found = None
        if found:
            return {
                "code": found.get("指标编码"),
                "name": found.get("指标名称"),
                "domain": found.get("业务域"),
                "caliber": found.get("计算口径"),
                "aggregator": found.get("聚合函数"),
                "granularity": found.get("数据粒度"),
            }
    return {}


# ---------------------------------------------------------------------------
# 绑定核验
# ---------------------------------------------------------------------------


def _build_binding(
    import_record: dict[str, Any], card: dict[str, Any],
) -> dict[str, Any]:
    binding_id = registry.next_binding_code()
    source_metric = card["metric"]
    target_metric = None
    target_matches = 0
    if source_metric["name"]:
        candidates = [
            row for row in metric_service.list_domain_metrics(import_record["target_domain"])
            if _same_text(row.get("指标名称"), source_metric["name"])
        ]
        target_matches = len(candidates)
        if len(candidates) == 1:
            target_metric = metric_service.resolve_metric_caliber(str(candidates[0].get("指标编码")))

    pending: list[dict[str, Any]] = []
    if target_matches == 0:
        pending.append(_pending_item(
            "target_metric_missing", "指标名称",
            f"目标域「{import_record['target_domain']}」没有名为「{source_metric['name']}」的指标",
            source_metric["name"], None,
        ))
    elif target_matches > 1:
        pending.append(_pending_item(
            "ambiguous_target_metric", "指标名称",
            f"目标域存在 {target_matches} 个同名指标，无法自动绑定",
            source_metric["name"], None,
        ))
    else:
        assert target_metric is not None
        if not _granularity_compatible(source_metric["granularity"], target_metric["granularity"]):
            pending.append(_pending_item(
                "granularity_mismatch", "数据粒度",
                f"导入口径为「{source_metric['granularity']}」，目标域指标为「{target_metric['granularity']}」",
                source_metric["granularity"], target_metric["granularity"],
            ))
        if str(source_metric["aggregator"]).upper() != str(target_metric["aggregator"]).upper():
            pending.append(_pending_item(
                "aggregator_mismatch", "聚合函数",
                f"导入口径聚合函数为 {source_metric['aggregator']}，目标域指标为 {target_metric['aggregator']}",
                source_metric["aggregator"], target_metric["aggregator"],
            ))
        if not _same_text(source_metric["caliber"], target_metric["caliber"]):
            pending.append(_pending_item(
                "caliber_mismatch", "计算口径",
                "导入文件里的计算口径描述与目标域指标不一致",
                source_metric["caliber"], target_metric["caliber"],
            ))
        ledger = registry.find_verification(
            target_metric["code"],
            source_metric["granularity"],
            source_metric["aggregator"],
        )
        if ledger is None:
            pending.append(_pending_item(
                "unverified_in_target", "核验台账",
                "目标域没有与该粒度、聚合函数匹配的核验记录；同名不算已确认",
                f"{source_metric['granularity']} / {source_metric['aggregator']}",
                None,
            ))

    status = "已确认" if target_metric and not pending else (
        "待核" if target_metric else "未匹配"
    )
    matched_ledger = None
    if target_metric and not pending:
        matched_ledger = registry.find_verification(
            target_metric["code"], source_metric["granularity"], source_metric["aggregator"],
        )

    binding = {
        "binding_id": binding_id,
        "import_id": import_record["id"],
        "card_id": card["card_id"],
        "card_title": card["title"],
        "metric_name": source_metric["name"],
        "source_metric_code": source_metric["code"],
        "target_metric_code": target_metric["code"] if target_metric else None,
        "source": dict(source_metric),
        "target": target_metric,
        "status": status,
        # 同名只是候选关系；只有目标域核验台账或人工核准才算确认。
        "match_basis": (
            "none" if not target_metric
            else "target_ledger" if matched_ledger
            else "same_name_unconfirmed"
        ),
        "name_only_match": bool(target_metric) and matched_ledger is None,
        "confirmation_basis": (
            "目标域核验台账" if matched_ledger else None
        ),
        "confirmed_by": matched_ledger.get("verified_by") if matched_ledger else None,
        "confirmed_at": matched_ledger.get("verified_at") if matched_ledger else None,
        "confirmed_granularity": source_metric["granularity"] if matched_ledger else None,
        "confirmed_aggregator": source_metric["aggregator"] if matched_ledger else None,
        "confirmed_caliber_version": (
            target_metric.get("caliber_version") if matched_ledger else None
        ),
        "stale": False,
        "pending_items": pending,
        "resolved_items": [],
        "created_at": _now(),
        "updated_at": _now(),
    }
    return binding


def _live_target(binding: dict[str, Any]) -> dict[str, Any] | None:
    """按目标指标编码取当前定义；取不到时退回绑定创建时的快照。"""
    code = binding.get("target_metric_code")
    if code:
        current = metric_service.resolve_metric_caliber(code)
        if current:
            return current
    return binding.get("target")


def _effective_binding(binding: dict[str, Any]) -> dict[str, Any]:
    """确定本次计算实际采用的绑定粒度 / 聚合函数 / 指标编码。

    已核验绑定用核验时记录的口径版本；未核验或已失活的绑定跟随目标域
    当前定义，但仍标记为待核，不进入发布范围。
    """
    target = _live_target(binding)
    if binding["status"] == "已确认" and not binding.get("stale"):
        return {
            "metric_code": binding["target_metric_code"] or binding["source_metric_code"],
            "metric_name": binding["metric_name"],
            "granularity": binding["confirmed_granularity"],
            "aggregator": binding["confirmed_aggregator"],
            "caliber_version": (
                target.get("caliber_version") if target else binding.get("confirmed_caliber_version")
            ),
            "verification_status": "已核验",
        }
    if target:
        return {
            "metric_code": binding["target_metric_code"],
            "metric_name": binding["metric_name"],
            "granularity": _pick_granularity(target.get("granularity")),
            "aggregator": target.get("aggregator"),
            "caliber_version": target.get("caliber_version"),
            "verification_status": "待核",
        }
    return {
        "metric_code": binding.get("source_metric_code"),
        "metric_name": binding["metric_name"],
        "granularity": _pick_granularity(binding["source"].get("granularity")),
        "aggregator": binding["source"].get("aggregator"),
        "caliber_version": None,
        "verification_status": "未匹配",
    }


# ---------------------------------------------------------------------------
# 指标值计算
# ---------------------------------------------------------------------------


def _parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value))
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _bucket_key(moment: datetime | None, granularity: str) -> str:
    if moment is None:
        return "全部"
    granularity = str(granularity or "").replace("按", "").strip()
    if granularity == "小时":
        return moment.strftime("%Y-%m-%d %H")
    if granularity == "日":
        return moment.strftime("%Y-%m-%d")
    if granularity == "周":
        year, week, _ = moment.isocalendar()
        return f"{year}-W{week:02d}"
    if granularity == "月":
        return moment.strftime("%Y-%m")
    if granularity == "季":
        return f"{moment.year}-Q{(moment.month - 1) // 3 + 1}"
    if granularity == "年":
        return moment.strftime("%Y")
    return moment.strftime("%Y-%m-%d")


def _compute_value(card: dict[str, Any], granularity: str, aggregator: str) -> dict[str, Any]:
    """按指定粒度 + 聚合函数计算卡片值。

    先按粒度分桶聚合，再按指标是否可累加汇总：
    SUM 取期内合计，AVG/MAX/MIN 取各桶结果再汇总，DISTINCT_COUNT 取期内去重。
    """
    rows = card["data"]["rows"]
    value_field = card["data"]["value_field"]
    time_field = card["data"]["time_field"]
    if not rows:
        return {"value": None, "bucket_values": [], "note": "文件未附带示例数据"}

    buckets: dict[str, list[Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        moment = _parse_time(row.get(time_field)) if time_field else None
        buckets.setdefault(_bucket_key(moment, granularity), []).append(row.get(value_field))

    numeric: dict[str, list[float]] = {}
    for key, values in buckets.items():
        prepared: list[float] = []
        for value in values:
            try:
                prepared.append(float(value))
            except (TypeError, ValueError):
                continue
        numeric[key] = prepared

    if aggregator == "DISTINCT_COUNT":
        distinct_all = len({str(value) for values in buckets.values() for value in values})
        bucket_values = [len({str(v) for v in values}) for values in buckets.values()]
        value = float(distinct_all)
        note = f"{granularity}分桶，期内去重计数"
    elif aggregator == "COUNT":
        value = float(sum(len(values) for values in buckets.values()))
        bucket_values = [float(len(values)) for values in buckets.values()]
        note = f"{granularity}分桶后合计行数"
    elif aggregator in ("SUM", "AVG", "MAX", "MIN"):
        bucket_values = [
            compute_metric(numeric[key], aggregator) for key in buckets
        ]
        if aggregator == "SUM":
            value = sum(bucket_values)
            note = f"{granularity}分桶汇总后取合计"
        elif aggregator == "AVG":
            value = sum(bucket_values) / len(bucket_values) if bucket_values else 0.0
            note = f"{granularity}分桶汇总后取均值"
        else:
            value = compute_metric(bucket_values, aggregator) if bucket_values else 0.0
            note = f"{granularity}分桶汇总后取{aggregator}"
    else:
        values = [v for chunk in numeric.values() for v in chunk]
        value = compute_metric(values, aggregator) if values else 0.0
        bucket_values = []
        note = f"未知聚合函数 {aggregator}，按原始值计算"

    return {
        "value": round(float(value), 2),
        "bucket_count": len(buckets),
        "bucket_values": [round(float(v), 2) for v in bucket_values],
        "note": note,
    }


def _card_preview(card: dict[str, Any], binding: dict[str, Any]) -> dict[str, Any]:
    source = binding["source"]
    preview: dict[str, Any] = {
        "imported": _compute_value(
            card, _pick_granularity(source.get("granularity")), str(source.get("aggregator") or ""),
        ),
        "target": None,
        "effective": None,
    }
    live_target = _live_target(binding)
    if live_target:
        preview["target"] = _compute_value(
            card, _pick_granularity(live_target.get("granularity")), str(live_target.get("aggregator") or ""),
        )
    effective = _effective_binding(binding)
    preview["effective"] = _compute_value(
        card, effective["granularity"], str(effective["aggregator"] or ""),
    )
    preview["effective_binding"] = effective
    return preview


# ---------------------------------------------------------------------------
# 导入主流程
# ---------------------------------------------------------------------------


def import_layout(
    *,
    layout: dict[str, Any] | str | None = None,
    path: str | None = None,
    target_domain: str | None = None,
    created_by: str = "数据工程组",
) -> dict[str, Any]:
    document, source = load_layout_document(layout, path)
    if not isinstance(document, dict):
        raise LayoutImportError("布局文件顶层必须是对象")

    raw_cards = document.get("cards") or document.get("卡片") or document.get("图表列表")
    if not isinstance(raw_cards, list) or not raw_cards:
        raise LayoutImportError("布局文件里没有卡片（cards）")

    dashboard_name = str(
        _first(document, ("dashboard_name", "看板名称", "name"), "")
    ).strip()
    if not dashboard_name:
        raise LayoutImportError("布局文件缺少看板名称")

    source_domain = str(
        _first(document, ("source_domain", "所属业务域", "source"), "")
    ).strip()
    target = str(
        target_domain
        or _first(document, ("target_domain", "目标业务域", "target"), "")
    ).strip()
    if not target:
        raise LayoutImportError("缺少目标业务域，无法做跨域核验")
    if not source_domain:
        source_domain = str(_first(raw_cards[0], ("domain",), ""))

    refresh_interval = _first(document, ("refresh_interval", "数据刷新间隔"), "")

    record: dict[str, Any] = {
        "id": registry.next_id("import"),
        "dashboard_name": dashboard_name,
        "source_domain": source_domain,
        "target_domain": target,
        "refresh_interval": str(refresh_interval or ""),
        "source_file": source,
        "created_by": created_by,
        "imported_at": _now(),
        "cards": [],
        "subscription_id": None,
        "dashboard_layout_id": None,
        "status": "待核项",
    }
    registry.imports.append(record)

    for index, raw in enumerate(raw_cards):
        if not isinstance(raw, dict):
            raise LayoutImportError(f"第 {index + 1} 张卡片不是对象")
        card = _parse_card(raw, index, source_domain)
        resolved = _resolve_source_metric(card["metric"], card["metric"]["domain"] or source_domain)
        if resolved:
            card["metric"]["name"] = card["metric"]["name"] or str(resolved.get("name") or "")
            card["metric"]["code"] = card["metric"]["code"] or str(resolved.get("code") or "")
            card["metric"]["caliber"] = card["metric"]["caliber"] or str(resolved.get("caliber") or "")
            card["metric"]["aggregator"] = card["metric"]["aggregator"] or str(
                resolved.get("aggregator") or ""
            )
            card["metric"]["granularity"] = card["metric"]["granularity"] or str(
                resolved.get("granularity") or ""
            )
        if not card["metric"]["name"]:
            raise LayoutImportError(f"第 {index + 1} 张卡片缺少指标名称")

        binding = _build_binding(record, card)
        registry.bindings.append(binding)
        card["binding_id"] = binding["binding_id"]
        record["cards"].append(card)

    pending_count = sum(len(item["pending_items"]) for item in _bindings_of(record["id"]))
    confirmed_count = sum(
        1 for item in _bindings_of(record["id"]) if item["status"] == "已确认"
    )
    record["pending_count"] = pending_count
    record["confirmed_count"] = confirmed_count
    record["status"] = (
        "已全部核验" if pending_count == 0 else
        ("部分待核" if confirmed_count else "待核项")
    )

    record["dashboard_layout_id"] = _register_dashboard_layout(record)
    record["subscription"] = create_subscription(record["id"])
    record["subscription_id"] = record["subscription"]["id"]
    return detail(record["id"])


def _bindings_of(import_id: int) -> list[dict[str, Any]]:
    return [item for item in registry.bindings if int(item["import_id"]) == int(import_id)]


def _register_dashboard_layout(record: dict[str, Any]) -> int | None:
    """把导入的布局登记到目标域的看板布局模块，便于从常规工作台继续管理。"""
    rows = store.rows("dashboard_layout")
    name = record["dashboard_name"]
    if any(row.get("看板名称") == name and row.get("所属业务域") == record["target_domain"] for row in rows):
        name = f"{name}（导入）"
    layout_text = ";".join(
        f"{card['type']}:{card['title']}:{card['layout']['w']}:{card['layout']['h']}:"
        f"{card['layout']['x']}:{card['layout']['y']}"
        for card in record["cards"]
    )
    entry = {
        "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
        "status": "草稿",
        "pending": True,
        "abnormal": False,
        "看板名称": name,
        "所属业务域": record["target_domain"],
        "图表布局": layout_text,
        "数据刷新间隔": record["refresh_interval"] or "每日",
        "是否公开": "否",
        "创建人": record["created_by"],
        "上次修改": record["imported_at"][:10],
        "_import_id": record["id"],
    }
    rows.append(entry)
    return int(entry["id"])


def create_subscription(
    import_id: int,
    *,
    name: str | None = None,
    schedule: str = "每日09:00",
    channel: str = "邮件",
    recipients: str = "data-ops@example.com",
    push_format: str = "CSV",
) -> dict[str, Any]:
    record = registry.find_import(import_id)
    if record is None:
        raise LayoutImportError(f"布局导入 {import_id} 不存在")
    subscription = {
        "id": registry.next_id("subscription"),
        "import_id": import_id,
        "name": name or (
            record["dashboard_name"]
            if record["dashboard_name"].endswith(DEFAULT_SUBSCRIPTION_NAME_SUFFIX)
            else f"{record['dashboard_name']}{DEFAULT_SUBSCRIPTION_NAME_SUFFIX}"
        ),
        "report_object": record["dashboard_name"],
        "target_domain": record["target_domain"],
        "schedule": schedule,
        "channel": channel,
        "recipients": recipients,
        "format": push_format,
        "status": "已启用",
        "last_sent_at": None,
        "created_at": _now(),
    }
    registry.subscriptions.append(subscription)
    return subscription


def _snapshot_of(binding: dict[str, Any]) -> dict[str, Any]:
    effective = _effective_binding(binding)
    return {
        "card_id": binding["card_id"],
        "card_title": binding["card_title"],
        "binding_id": binding["binding_id"],
        "metric_name": binding["metric_name"],
        "metric_code": effective["metric_code"],
        "granularity": effective["granularity"],
        "aggregator": effective["aggregator"],
        "caliber_version": effective["caliber_version"],
        "verification_status": effective["verification_status"],
    }


def _report_rows(record: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    for card in record["cards"]:
        binding = registry.binding_for_import_card(record["id"], card["card_id"])
        assert binding is not None
        effective = _effective_binding(binding)
        snapshot = _snapshot_of(binding)
        snapshots.append(snapshot)
        verified = binding["status"] == "已确认" and not binding.get("stale")
        value = None
        if verified:
            value = _compute_value(
                card, effective["granularity"], str(effective["aggregator"] or ""),
            )["value"]
        pending_messages = [item["message"] for item in binding.get("pending_items", [])]
        rows.append({
            "card_id": card["card_id"],
            "title": card["title"],
            "metric_name": binding["metric_name"],
            "metric_code": effective["metric_code"] or "",
            "binding_id": binding["binding_id"],
            "granularity": effective["granularity"] or "",
            "aggregator": effective["aggregator"] or "",
            "caliber_version": effective["caliber_version"],
            "value": value,
            "unit": card["data"].get("unit", ""),
            "verification_status": effective["verification_status"],
            "publish_state": "已发布" if verified else "待核不发布",
            "pending_items": pending_messages,
            "pending_item_codes": [item["code"] for item in binding.get("pending_items", [])],
        })
    return rows, snapshots


def send_daily_report(
    subscription_id: int,
    *,
    report_date: str | None = None,
    trigger: str = "manual",
) -> dict[str, Any]:
    subscription = registry.find_subscription(subscription_id)
    if subscription is None:
        raise LayoutImportError(f"订阅 {subscription_id} 不存在")
    if subscription["status"] in ("已暂停", "已停用"):
        raise LayoutImportError(f"订阅「{subscription['name']}」状态为{subscription['status']}，不能投递")
    record = registry.find_import(int(subscription["import_id"]))
    if record is None:
        raise LayoutImportError(f"订阅关联的布局导入 {subscription['import_id']} 不存在")

    report_date = report_date or datetime.now().strftime("%Y-%m-%d")
    rows, snapshots = _report_rows(record)
    published = sum(1 for row in rows if row["publish_state"] == "已发布")
    withheld = len(rows) - published
    pending_total = sum(len(row["pending_items"]) for row in rows)

    delivery_id = registry.next_id("delivery")
    file_name = (
        f"{_safe_name(record['dashboard_name'])}_{report_date}_D{delivery_id:03d}.csv"
    )
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    file_path = REPORT_DIR / file_name
    content = _render_csv(record, subscription, delivery_id, report_date, rows)
    file_path.write_text(content, encoding="utf-8")

    delivery = {
        "id": delivery_id,
        "subscription_id": subscription_id,
        "subscription_name": subscription["name"],
        "import_id": record["id"],
        "dashboard_name": record["dashboard_name"],
        "target_domain": record["target_domain"],
        "report_date": report_date,
        "sent_at": _now(),
        "trigger": trigger,
        "file_name": file_name,
        "file_path": str(file_path),
        "file_size": len(content.encode("utf-8")),
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()[:16],
        "rows": rows,
        "binding_snapshot": snapshots,
        "published_count": published,
        "withheld_count": withheld,
        "pending_count": pending_total,
        "status": "已发送",
    }
    registry.deliveries.append(delivery)
    subscription["last_sent_at"] = delivery["sent_at"]
    subscription["last_delivery_id"] = delivery_id
    return delivery_detail(delivery_id)


def _render_csv(
    record: dict[str, Any],
    subscription: dict[str, Any],
    delivery_id: int,
    report_date: str,
    rows: list[dict[str, Any]],
) -> str:
    output = io.StringIO()
    output.write("﻿")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow([
        "report_date", "dashboard_name", "target_domain", "delivery_id", "subscription_id",
        "card_id", "title", "metric_name", "metric_code", "binding_id",
        "granularity", "aggregator", "caliber_version", "value", "unit",
        "verification_status", "publish_state", "pending_items",
    ])
    for row in rows:
        writer.writerow([
            report_date, record["dashboard_name"], record["target_domain"], delivery_id,
            subscription["id"], row["card_id"], row["title"], row["metric_name"],
            row["metric_code"], row["binding_id"], row["granularity"], row["aggregator"],
            row["caliber_version"] if row["caliber_version"] is not None else "",
            _fmt_number(row["value"]), row["unit"], row["verification_status"],
            row["publish_state"], " | ".join(row["pending_items"]),
        ])
    return output.getvalue()


# ---------------------------------------------------------------------------
# 核验 / 查询 / 对比
# ---------------------------------------------------------------------------


def confirm_binding(
    import_id: int,
    binding_id: str,
    *,
    granularity: str,
    aggregator: str | None = None,
    metric_code: str | None = None,
    verified_by: str = "数据治理组",
    note: str = "",
) -> dict[str, Any]:
    """人工核准绑定：按目标域指标的真实粒度确认，并落入核验台账。"""
    record = registry.find_import(import_id)
    if record is None:
        raise LayoutImportError(f"布局导入 {import_id} 不存在")
    binding = registry.find_binding(binding_id)
    if binding is None or int(binding["import_id"]) != int(import_id):
        raise LayoutImportError(f"绑定 {binding_id} 不属于布局导入 {import_id}")

    # 兼容「按小时」「小时」「按小时/按日」等写法，只取第一个可识别粒度
    chosen_units = _granularity_units(granularity)[:1]
    if not chosen_units:
        raise LayoutImportError(
            f"粒度「{granularity}」无法识别，应为 {'/'.join(GRANULARITY_ORDER)} 之一"
        )
    chosen_granularity = f"按{chosen_units[0]}"

    code = metric_code or binding.get("target_metric_code")
    target = metric_service.resolve_metric_caliber(code) if code else None
    if target is None:
        # 目标域没有同名指标时，允许按名称人工确认一次，但仍然要校验粒度
        candidates = [
            row for row in metric_service.list_domain_metrics(record["target_domain"])
            if _same_text(row.get("指标名称"), binding["metric_name"])
        ]
        if len(candidates) == 1:
            target = metric_service.resolve_metric_caliber(str(candidates[0].get("指标编码")))
    if target is None:
        raise LayoutImportError(
            f"目标域「{record['target_domain']}」没有可用于核验的指标"
            f"（指标名：{binding['metric_name']}）"
        )
    if target["domain"] != record["target_domain"]:
        raise LayoutImportError(
            f"指标 {target['code']} 属于「{target['domain']}」，不是目标域「{record['target_domain']}」"
        )

    allowed_units = _granularity_units(target["granularity"])
    if allowed_units and chosen_units[0] not in allowed_units:
        raise LayoutImportError(
            f"目标指标「{target['name']}」支持的粒度为 {'/'.join(allowed_units)}，"
            f"不能核准为 {chosen_granularity}"
        )
    chosen_aggregator = str(aggregator or target["aggregator"]).upper()
    if chosen_aggregator != str(target["aggregator"]).upper():
        raise LayoutImportError(
            f"目标指标「{target['name']}」的聚合函数为 {target['aggregator']}，"
            f"不能核准为 {chosen_aggregator}"
        )

    resolved = list(binding.get("pending_items", []))
    previous_status = binding["status"]
    binding["target_metric_code"] = target["code"]
    binding["target"] = target
    binding["status"] = "已确认"
    binding["stale"] = False
    binding["confirmed_granularity"] = chosen_granularity
    binding["confirmed_aggregator"] = chosen_aggregator
    binding["confirmed_caliber_version"] = target["caliber_version"]
    binding["confirmed_by"] = verified_by
    binding["confirmed_at"] = _now()
    binding["confirmation_basis"] = "人工核准（目标域指标粒度）"
    binding["name_only_match"] = True
    binding["pending_items"] = []
    binding["resolved_items"] = resolved
    binding["resolution_note"] = note
    binding["updated_at"] = _now()

    registry.record_verification({
        "target_metric_code": target["code"],
        "target_domain": target["domain"],
        "granularity": binding["confirmed_granularity"],
        "aggregator": chosen_aggregator,
        "caliber_version": target["caliber_version"],
        "verified_by": verified_by,
        "verified_at": _now(),
        "note": note or f"由布局导入 {import_id} 的绑定 {binding_id} 核准",
        "source_binding": binding_id,
    })
    record["pending_count"] = sum(len(item["pending_items"]) for item in _bindings_of(import_id))
    record["confirmed_count"] = sum(
        1 for item in _bindings_of(import_id) if item["status"] == "已确认"
    )
    record["status"] = (
        "已全部核验" if record["pending_count"] == 0 else
        ("部分待核" if record["confirmed_count"] else "待核项")
    )
    return {
        "ok": True,
        "binding": binding,
        "previous_status": previous_status,
        "resolved_items": resolved,
        "preview": _card_preview(_card_of(record, binding["card_id"]), binding),
        "import_status": record["status"],
    }


def _card_of(record: dict[str, Any], card_id: str) -> dict[str, Any]:
    for card in record["cards"]:
        if card["card_id"] == card_id:
            return card
    raise LayoutImportError(f"布局导入 {record['id']} 里没有卡片 {card_id}")


def _binding_view(binding: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
    preview = _card_preview(card, binding)
    return {
        **binding,
        "effective_binding": preview["effective_binding"],
        "preview": preview,
    }


def detail(import_id: int) -> dict[str, Any]:
    record = registry.find_import(import_id)
    if record is None:
        raise LayoutImportError(f"布局导入 {import_id} 不存在")
    bindings = _bindings_of(import_id)
    cards = []
    for card in record["cards"]:
        binding = registry.binding_for_import_card(import_id, card["card_id"])
        if binding is None:
            continue
        cards.append({
            "card_id": card["card_id"],
            "title": card["title"],
            "type": card["type"],
            "layout": card["layout"],
            "metric": card["metric"],
            "data_summary": {
                "row_count": len(card["data"]["rows"]),
                "time_field": card["data"]["time_field"],
                "value_field": card["data"]["value_field"],
                "unit": card["data"]["unit"],
            },
            "import_claims": card.get("import_claims", {}),
            "binding": _binding_view(binding, card),
        })
    pending_items = [
        {"binding_id": item["binding_id"], "card_id": item["card_id"], **pending}
        for item in bindings for pending in item["pending_items"]
    ]
    subscription = (
        registry.find_subscription(int(record["subscription_id"]))
        if record.get("subscription_id") else None
    )
    deliveries = [
        _delivery_summary(item) for item in registry.deliveries
        if int(item["import_id"]) == int(import_id)
    ]
    return {
        "ok": True,
        "id": record["id"],
        "dashboard_name": record["dashboard_name"],
        "source_domain": record["source_domain"],
        "target_domain": record["target_domain"],
        "refresh_interval": record["refresh_interval"],
        "source_file": record["source_file"],
        "imported_at": record["imported_at"],
        "status": record["status"],
        "dashboard_layout_id": record.get("dashboard_layout_id"),
        "card_count": len(record["cards"]),
        "pending_count": len(pending_items),
        "confirmed_count": sum(1 for item in bindings if item["status"] == "已确认"),
        "cards": cards,
        "pending_items": pending_items,
        "bindings": bindings,
        "subscription": subscription,
        "deliveries": deliveries,
    }


def list_imports() -> dict[str, Any]:
    items = [
        {
            "id": record["id"],
            "dashboard_name": record["dashboard_name"],
            "source_domain": record["source_domain"],
            "target_domain": record["target_domain"],
            "status": record["status"],
            "card_count": len(record["cards"]),
            "pending_count": record.get("pending_count", 0),
            "confirmed_count": record.get("confirmed_count", 0),
            "imported_at": record["imported_at"],
            "subscription_id": record.get("subscription_id"),
        }
        for record in reversed(registry.imports)
    ]
    return {"ok": True, "items": items, "total": len(items)}


def binding_snapshot_for_import(import_id: int) -> list[dict[str, Any]]:
    """看板布局详情页用的绑定视图：与订阅日报共用同一份快照结构。"""
    record = registry.find_import(import_id)
    if record is None:
        return []
    snapshots = []
    for card in record["cards"]:
        binding = registry.binding_for_import_card(import_id, card["card_id"])
        if binding is None:
            continue
        snapshot = _snapshot_of(binding)
        snapshot["status"] = binding["status"]
        snapshot["confirmed_by"] = binding.get("confirmed_by")
        snapshot["confirmed_at"] = binding.get("confirmed_at")
        snapshot["pending_item_codes"] = [item["code"] for item in binding["pending_items"]]
        snapshots.append(snapshot)
    return snapshots


def _delivery_summary(delivery: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": delivery["id"],
        "subscription_id": delivery["subscription_id"],
        "subscription_name": delivery["subscription_name"],
        "import_id": delivery["import_id"],
        "dashboard_name": delivery["dashboard_name"],
        "target_domain": delivery["target_domain"],
        "report_date": delivery["report_date"],
        "sent_at": delivery["sent_at"],
        "file_name": delivery["file_name"],
        "published_count": delivery["published_count"],
        "withheld_count": delivery["withheld_count"],
        "pending_count": delivery["pending_count"],
        "values": [
            {
                "card_id": row["card_id"],
                "title": row["title"],
                "binding_id": row["binding_id"],
                "value": row["value"],
                "publish_state": row["publish_state"],
            }
            for row in delivery["rows"]
        ],
    }


def delivery_detail(delivery_id: int) -> dict[str, Any]:
    delivery = registry.find_delivery(delivery_id)
    if delivery is None:
        raise LayoutImportError(f"日报投递 {delivery_id} 不存在")
    path = Path(delivery["file_path"])
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    return {
        "ok": True,
        **delivery,
        "file_url": f"/api/layout-imports/deliveries/{delivery_id}/file",
        "file_content": content,
        "summary": _delivery_summary(delivery),
    }


def list_deliveries(import_id: int | None = None) -> dict[str, Any]:
    items = [
        _delivery_summary(item) for item in reversed(registry.deliveries)
        if import_id is None or int(item["import_id"]) == int(import_id)
    ]
    return {"ok": True, "items": items, "total": len(items)}


def compare_deliveries(left_id: int, right_id: int) -> dict[str, Any]:
    """逐卡对比两次日报的数值与待核项，用于核准前后核验。"""
    left = registry.find_delivery(left_id)
    right = registry.find_delivery(right_id)
    if left is None or right is None:
        raise LayoutImportError("对比的日报投递不存在")

    left_rows = {row["card_id"]: row for row in left["rows"]}
    right_rows = {row["card_id"]: row for row in right["rows"]}
    cards: list[dict[str, Any]] = []
    for card_id in list(dict.fromkeys([*left_rows, *right_rows])):
        left_row = left_rows.get(card_id, {})
        right_row = right_rows.get(card_id, {})
        left_pending = set(left_row.get("pending_item_codes", []))
        right_pending = set(right_row.get("pending_item_codes", []))
        cards.append({
            "card_id": card_id,
            "title": left_row.get("title") or right_row.get("title"),
            "metric_name": left_row.get("metric_name") or right_row.get("metric_name"),
            "left": {
                "delivery_id": left["id"],
                "binding_id": left_row.get("binding_id"),
                "granularity": left_row.get("granularity"),
                "aggregator": left_row.get("aggregator"),
                "caliber_version": left_row.get("caliber_version"),
                "value": left_row.get("value"),
                "publish_state": left_row.get("publish_state"),
                "pending_item_codes": sorted(left_pending),
                "pending_items": left_row.get("pending_items", []),
            },
            "right": {
                "delivery_id": right["id"],
                "binding_id": right_row.get("binding_id"),
                "granularity": right_row.get("granularity"),
                "aggregator": right_row.get("aggregator"),
                "caliber_version": right_row.get("caliber_version"),
                "value": right_row.get("value"),
                "publish_state": right_row.get("publish_state"),
                "pending_item_codes": sorted(right_pending),
                "pending_items": right_row.get("pending_items", []),
            },
            "value_changed": left_row.get("value") != right_row.get("value"),
            "pending_resolved": sorted(left_pending - right_pending),
            "pending_added": sorted(right_pending - left_pending),
        })
    return {
        "ok": True,
        "left": _delivery_summary(left),
        "right": _delivery_summary(right),
        "cards": cards,
        "summary": {
            "left_published": left["published_count"],
            "right_published": right["published_count"],
            "left_pending": left["pending_count"],
            "right_pending": right["pending_count"],
        },
    }


# ---------------------------------------------------------------------------
# 口径变更与重算
# ---------------------------------------------------------------------------


def change_caliber(
    metric_code: str,
    *,
    caliber: str,
    aggregator: str | None = None,
    granularity: str | None = None,
    changed_by: str = "数据治理组",
) -> dict[str, Any]:
    """变更目标指标口径，并把使用该指标的已确认绑定标记为待复核。"""
    change = metric_service.update_caliber(
        metric_code,
        caliber=caliber,
        aggregator=aggregator,
        granularity=granularity,
        changed_by=changed_by,
    )
    if change is None:
        raise LayoutImportError(f"指标 {metric_code} 不存在")

    affected: list[dict[str, Any]] = []
    for binding in registry.bindings:
        target_code = binding.get("target_metric_code")
        if target_code != metric_code:
            continue
        if binding["status"] != "已确认":
            continue
        previous_version = binding.get("confirmed_caliber_version")
        binding["stale"] = True
        binding["status"] = "待复核"
        binding["pending_items"].append(_pending_item(
            "caliber_changed", "计算口径",
            f"目标指标口径已从 v{previous_version} 变更为 v{change['after']['caliber_version']}，绑定需要重新核验",
            previous_version, change["after"]["caliber_version"],
        ))
        binding["updated_at"] = _now()
        affected.append({
            "binding_id": binding["binding_id"],
            "import_id": binding["import_id"],
            "card_id": binding["card_id"],
            "previous_caliber_version": previous_version,
            "current_caliber_version": change["after"]["caliber_version"],
        })

    affected_deliveries = [
        {
            "delivery_id": item["delivery"]["id"],
            "import_id": item["delivery"]["import_id"],
            "card_id": item["snapshot"]["card_id"],
            "binding_id": item["snapshot"]["binding_id"],
            "caliber_version": item["snapshot"]["caliber_version"],
        }
        for item in registry.deliveries_using_metric(metric_code)
    ]
    registry.caliber_events.append({**change, "affected_bindings": affected})
    return {
        "ok": True,
        "change": change,
        "affected_bindings": affected,
        "affected_deliveries": affected_deliveries,
    }


def recalc_metric(metric_code: str) -> dict[str, Any]:
    """口径变更后按绑定重算：输出实际使用的 binding_id 和引用它的日报。"""
    target = metric_service.resolve_metric_caliber(metric_code)
    if target is None:
        raise LayoutImportError(f"指标 {metric_code} 不存在")

    binding_results: list[dict[str, Any]] = []
    for binding in registry.bindings:
        codes = {binding.get("target_metric_code"), binding.get("source_metric_code")}
        if metric_code not in codes:
            continue
        record = registry.find_import(int(binding["import_id"]))
        card = _card_of(record, binding["card_id"])
        effective = _effective_binding(binding)
        computed = _compute_value(
            card, effective["granularity"], str(effective["aggregator"] or ""),
        )
        used_by = [
            {
                "delivery_id": item["delivery"]["id"],
                "report_date": item["delivery"]["report_date"],
                "file_name": item["delivery"]["file_name"],
                "value": item["value"],
                "publish_state": item["publish_state"],
                "granularity": item["snapshot"].get("granularity"),
                "aggregator": item["snapshot"].get("aggregator"),
                "caliber_version": item["snapshot"].get("caliber_version"),
                "binding_id": item["snapshot"].get("binding_id"),
            }
            for item in registry.deliveries_using_binding(binding["binding_id"])
        ]
        binding_results.append({
            "binding_id": binding["binding_id"],
            "import_id": binding["import_id"],
            "card_id": binding["card_id"],
            "metric_name": binding["metric_name"],
            "status": binding["status"],
            "stale": binding.get("stale", False),
            "metric_code": effective["metric_code"],
            "granularity": effective["granularity"],
            "aggregator": effective["aggregator"],
            "caliber_version": effective["caliber_version"],
            "recalculated_value": computed["value"],
            "publish_state": (
                "可发布" if binding["status"] == "已确认" and not binding.get("stale")
                else "待复核不发布"
            ),
            "used_by_deliveries": used_by,
        })
    return {
        "ok": True,
        "metric_code": metric_code,
        "metric_name": target["name"],
        "current_caliber_version": target["caliber_version"],
        "current_granularity": target["granularity"],
        "current_aggregator": target["aggregator"],
        "bindings": binding_results,
        "binding_count": len(binding_results),
    }

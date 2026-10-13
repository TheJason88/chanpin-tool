import ast
import json
import re

import pandas as pd


STAGE2_DETAIL_SHEET = "派送二_匹配后批次数据"
TRANSFER_DETAIL_SHEET = "SO明细"
TRANSFER_BATCH_SHEET = "批次汇总"
TRANSFER_DETAIL_SHEET_ALIASES = ["SO明细", "调拨SO明细", "调拨明细", "调拨出库明细", "出库明细", "明细"]
TRANSFER_BATCH_SHEET_ALIASES = ["批次汇总", "调拨批次汇总", "批次统计", "批次明细汇总"]
TRANSFER_DETAIL_COLUMN_ALIASES = {
    "调拨线路": ["调拨线路", "调拨路线", "线路"],
    "出库批次号": ["出库批次号", "批次号", "出库批次"],
    "仓点": ["仓点", "仓点代码", "FBA仓点", "目的仓点", "目的仓"],
    "平台": ["平台", "平台名称", "目的平台"],
    "方数": ["方数", "出库体积", "体积", "CBM"],
}
TRANSFER_STATION_COST_COLUMNS = ["仓点分摊调拨成本", "仓点调拨成本", "分摊调拨成本"]
TRANSFER_UNIT_COST_COLUMNS = ["仓点调拨成本/方", "调拨成本/方", "调拨每方成本", "每方调拨成本", "每方成本"]
TRANSFER_BATCH_COST_COLUMNS = ["批次总调拨成本", "批次调拨成本", "批次总成本", "总调拨成本", "调拨成本"]
TRANSFER_KEYWORDS = ("仓间调拨", "调拨")
AMAZON_KEYWORDS = ("AMAZON", "亚马逊", "FBA")
STAGE2_PERIOD_COLUMNS = ("批次出库时间", "出库时间", "出库日期")
TRANSFER_PERIOD_COLUMNS = ("出库时间", "批次出库时间", "出库日期")


def _text(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null", "<na>"} else text


def _number(value):
    value = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return 0.0 if pd.isna(value) else float(value)


def _positive(value):
    value = _number(value)
    return value if value > 0 else 0.0


def _truthy(value):
    text = _text(value).upper()
    if text in {"", "TRUE", "1", "是", "有效", "Y", "YES"}:
        return True
    if text in {"FALSE", "0", "否", "无效", "N", "NO"}:
        return False
    return True


def _read_sheet(excel_file, sheet_name):
    if hasattr(excel_file, "seek"):
        excel_file.seek(0)
    xls = pd.ExcelFile(excel_file)
    if sheet_name not in xls.sheet_names:
        raise ValueError(f"文件中缺少工作表“{sheet_name}”。")
    if hasattr(excel_file, "seek"):
        excel_file.seek(0)
    frame = pd.read_excel(excel_file, sheet_name=sheet_name, dtype=object)
    frame.columns = [str(col).strip() for col in frame.columns]
    return frame


def _read_optional_sheet(excel_file, sheet_name):
    if hasattr(excel_file, "seek"):
        excel_file.seek(0)
    xls = pd.ExcelFile(excel_file)
    if sheet_name not in xls.sheet_names:
        return pd.DataFrame()
    if hasattr(excel_file, "seek"):
        excel_file.seek(0)
    frame = pd.read_excel(excel_file, sheet_name=sheet_name, dtype=object)
    frame.columns = [str(col).strip() for col in frame.columns]
    return frame


def _canonicalize_columns(frame, column_aliases=None):
    frame = frame.copy()
    frame.columns = [str(col).strip() for col in frame.columns]
    if not column_aliases:
        return frame
    normalized = {str(col).strip().lower(): col for col in frame.columns}
    rename = {}
    for canonical, aliases in column_aliases.items():
        if canonical in frame.columns:
            continue
        for alias in aliases:
            source = normalized.get(str(alias).strip().lower())
            if source is not None:
                rename[source] = canonical
                break
    return frame.rename(columns=rename)


def _read_matching_sheet(excel_file, aliases, required_columns, logical_name, optional=False, column_aliases=None):
    """Prefer known names, then identify a sheet by its required columns."""
    if hasattr(excel_file, "seek"):
        excel_file.seek(0)
    xls = pd.ExcelFile(excel_file)
    normalized_names = {str(name).strip().lower(): name for name in xls.sheet_names}
    selected = None
    for alias in aliases:
        candidate = normalized_names.get(str(alias).strip().lower())
        if candidate is None:
            continue
        header = pd.read_excel(xls, sheet_name=candidate, nrows=0)
        header = _canonicalize_columns(header, column_aliases)
        if set(required_columns).issubset(set(header.columns)):
            selected = candidate
            break
    if selected is None:
        for name in xls.sheet_names:
            header = pd.read_excel(xls, sheet_name=name, nrows=0)
            header = _canonicalize_columns(header, column_aliases)
            columns = set(header.columns)
            if set(required_columns).issubset(columns):
                selected = name
                break
    if selected is None:
        if optional:
            return pd.DataFrame()
        available = "、".join(str(name) for name in xls.sheet_names)
        required = "、".join(required_columns)
        raise ValueError(
            f"未识别到{logical_name}。现有工作表：{available or '无'}；"
            f"目标工作表至少需要字段：{required}。"
        )
    frame = pd.read_excel(xls, sheet_name=selected, dtype=object)
    return _canonicalize_columns(frame, column_aliases)


def _parse_allocations(value):
    if isinstance(value, list):
        return value
    text = _text(value)
    if not text:
        return []
    for loader in (json.loads, ast.literal_eval):
        try:
            result = loader(text)
            return result if isinstance(result, list) else []
        except Exception:
            continue
    return []


def _normalize_station(value):
    return re.sub(r"\s+", "", _text(value)).upper()


def _normalize_origin(value):
    text = _text(value).upper()
    for name in ("LA", "NJ", "SAV", "DAL"):
        if text == name or text.startswith(name + "→") or text.startswith(name + "-"):
            return name
    return text


def _route_origin(value):
    text = _text(value).upper().replace("->", "→").replace("—", "-")
    return _normalize_origin(re.split(r"→|-", text, maxsplit=1)[0])


def _contains_transfer(row):
    text = " ".join(_text(row.get(col, "")) for col in ["业务场景", "出库类型", "主产品类型", "系统产品类型"])
    return any(word in text for word in TRANSFER_KEYWORDS)


def _is_fba_platform(value):
    text = _text(value).upper()
    return any(word in text for word in AMAZON_KEYWORDS)


def _joined_unique(values):
    unique = []
    for value in values:
        text = _text(value)
        if text and text not in unique:
            unique.append(text)
    return "、".join(unique)


def _file_months(frame, candidates):
    """Return the set of YYYY-MM values represented by usable outbound dates."""
    for col in candidates:
        if col not in frame.columns:
            continue
        dates = pd.to_datetime(frame[col], errors="coerce").dropna()
        if not dates.empty:
            return set(dates.dt.to_period("M").astype(str))
    return set()


def _validate_source_periods(stage2, transfer):
    """Prevent an accidental cross-month merge when both files expose dates."""
    stage2_months = _file_months(stage2, STAGE2_PERIOD_COLUMNS)
    transfer_months = _file_months(transfer, TRANSFER_PERIOD_COLUMNS)
    if stage2_months and transfer_months and stage2_months != transfer_months:
        left = "、".join(sorted(stage2_months))
        right = "、".join(sorted(transfer_months))
        raise ValueError(
            f"派送三文件月份不一致：派送二为 {left}，调拨明细为 {right}。"
            "请上传同一月份的两个结果文件。"
        )
    if stage2_months and transfer_months:
        return "、".join(sorted(stage2_months)), "两份文件月份一致"
    return "未核验", "至少一份文件没有可识别的出库日期，已按批次号继续匹配"


def _first_existing(frame, candidates):
    for col in candidates:
        if col in frame.columns:
            return col
    return None


def _coalesce_cost(values):
    numbers = [_positive(value) for value in values]
    numbers = [value for value in numbers if value > 0]
    if not numbers:
        return 0.0
    unique = []
    for value in numbers:
        if not any(abs(value - seen) < 0.005 for seen in unique):
            unique.append(value)
    return unique[0] if len(unique) == 1 else sum(numbers)


def build_direct_facts(stage2_detail, warehouse="全部"):
    rows = []
    for _, row in stage2_detail.iterrows():
        origin = _normalize_origin(row.get("仓库", ""))
        if warehouse != "全部" and origin != warehouse:
            continue
        if _contains_transfer(row) or not _truthy(row.get("批次数据是否有效", True)):
            continue
        allocations = _parse_allocations(row.get("目的仓点分配明细", ""))
        fba_allocations = [item for item in allocations if _text(item.get("对象类型", "")).upper() == "FBA"]
        if not fba_allocations:
            codes = [_normalize_station(value) for value in re.split(r"[,，;；、|/\s]+", _text(row.get("FBA仓点代码集合", "")))]
            codes = [code for code in codes if code]
            if len(codes) == 1:
                volume = _positive(row.get("FBA出库体积", 0))
                total_volume = _positive(row.get("出库体积", 0))
                total_cost = _positive(row.get("派送成本", 0))
                fallback_cost = total_cost * volume / total_volume if total_volume > 0 else 0.0
                fba_allocations = [{"对象类型": "FBA", "仓点代码": codes[0], "出库体积": volume, "派送成本": fallback_cost}]
        for item in fba_allocations:
            station = _normalize_station(item.get("仓点代码", ""))
            volume = _positive(item.get("出库体积", 0))
            if not station or volume <= 0:
                continue
            cost = _positive(item.get("派送成本", 0))
            method = _text(row.get("仓点派送方式", "")) or _text(row.get("派送方式", "")) or "未识别"
            batch = _text(item.get("批次号", "")) or _text(row.get("批次号", "")) or _text(row.get("分析批次ID", ""))
            rows.append({
                "发货仓": origin,
                "FBA仓点": station,
                "来源": "直发",
                "派送方式": method,
                "批次号": batch,
                "线路": _text(row.get("专线线路", "")),
                "供应商": _text(row.get("派送卡车", "")),
                "方数": volume,
                "成本": cost,
                "成本有效方数": volume if cost > 0 else 0.0,
                "成本阶段": "直发派送",
                "成本来源": "派送二仓点分配明细" if cost > 0 else "缺失",
                "成本完整性": "直发口径",
            })
    return pd.DataFrame(rows)


def _build_stage2_batch_costs(stage2_detail, warehouse="全部"):
    result = {}
    for _, row in stage2_detail.iterrows():
        origin = _normalize_origin(row.get("仓库", ""))
        if warehouse != "全部" and origin != warehouse:
            continue
        cost = _positive(row.get("派送成本", 0))
        batch_values = [_text(row.get("批次号", ""))]
        batch_values.extend(re.split(r"[,，;；、|/\s]+", _text(row.get("批次号集合", ""))))
        batch_values = list(dict.fromkeys(batch for batch in map(_text, batch_values) if batch))
        common = {
            "供应商": _text(row.get("派送卡车", "")),
            "线路": _text(row.get("专线线路", "")),
        }
        if len(batch_values) == 1:
            result.setdefault(batch_values[0], {"成本": cost, **common})
            continue

        # A stage-two row may represent several batches. Never copy its full
        # vehicle cost to every batch. Use allocation-detail costs only when
        # they identify the batch explicitly; otherwise leave fallback absent.
        allocated = {}
        for item in _parse_allocations(row.get("目的仓点分配明细", "")):
            batch = _text(item.get("批次号", ""))
            item_cost = _positive(item.get("派送成本", 0))
            if batch in batch_values and item_cost > 0:
                allocated[batch] = allocated.get(batch, 0.0) + item_cost
        for batch, batch_cost in allocated.items():
            result.setdefault(batch, {"成本": batch_cost, **common})
    return result


def _build_transfer_batch_costs(batch_summary):
    result = {}
    if batch_summary.empty:
        return result
    cost_col = _first_existing(batch_summary, TRANSFER_BATCH_COST_COLUMNS)
    unit_cost_col = _first_existing(batch_summary, TRANSFER_UNIT_COST_COLUMNS)
    if not cost_col and not unit_cost_col:
        return result
    for _, row in batch_summary.iterrows():
        batch = _text(row.get("出库批次号", ""))
        if batch:
            if cost_col:
                cost = _positive(row.get(cost_col, 0))
            else:
                cost = _positive(row.get(unit_cost_col, 0)) * _positive(row.get("方数", 0))
            result[batch] = cost
    return result


def build_transfer_facts(transfer_detail, stage2_detail, transfer_batch_summary=None, warehouse="全部"):
    required = {"调拨线路", "出库批次号", "仓点", "平台", "方数"}
    missing = sorted(required - set(transfer_detail.columns))
    if missing:
        raise ValueError(f"调拨SO明细缺少字段：{'、'.join(missing)}。")
    work = transfer_detail.copy()
    work["发货仓"] = work["调拨线路"].apply(_route_origin)
    if warehouse != "全部":
        work = work[work["发货仓"] == warehouse].copy()
    work["方数数值"] = pd.to_numeric(work["方数"], errors="coerce").fillna(0).clip(lower=0)
    work["FBA标记"] = work["平台"].apply(_is_fba_platform)
    work["标准仓点"] = work["仓点"].apply(_normalize_station)
    station_cost_col = _first_existing(work, TRANSFER_STATION_COST_COLUMNS)
    unit_cost_col = _first_existing(work, TRANSFER_UNIT_COST_COLUMNS)
    batch_costs = _build_transfer_batch_costs(transfer_batch_summary if transfer_batch_summary is not None else pd.DataFrame())
    # 兼容后续把批次总成本直接带到SO明细的版本。
    if _first_existing(work, TRANSFER_BATCH_COST_COLUMNS):
        for batch, cost in _build_transfer_batch_costs(work).items():
            batch_costs.setdefault(batch, cost)
    stage2_costs = _build_stage2_batch_costs(stage2_detail, warehouse)

    grouped_rows = []
    keys = ["发货仓", "调拨线路", "出库批次号", "标准仓点", "平台", "FBA标记"]
    for key, group in work.groupby(keys, dropna=False, sort=False):
        origin, route, batch, station, platform, is_fba = key
        volume = float(group["方数数值"].sum())
        station_cost = _coalesce_cost(group[station_cost_col]) if station_cost_col else 0.0
        station_cost_source = "调拨明细仓点分摊成本" if station_cost > 0 else ""
        if station_cost <= 0 and unit_cost_col:
            unit_rate = pd.to_numeric(group[unit_cost_col], errors="coerce")
            positive_volume = group["方数数值"].gt(0)
            complete_unit_rate = positive_volume.any() and unit_rate.loc[positive_volume].gt(0).all()
            if complete_unit_rate:
                station_cost = float(
                    (unit_rate.loc[positive_volume] * group.loc[positive_volume, "方数数值"]).sum()
                )
                station_cost_source = "调拨明细仓点每方成本" if station_cost > 0 else ""
        grouped_rows.append({
            "发货仓": _text(origin),
            "线路": _text(route),
            "批次号": _text(batch),
            "FBA仓点": _text(station),
            "平台": _text(platform),
            "是否FBA": bool(is_fba),
            "方数": volume,
            "明细仓点成本": station_cost,
            "明细成本来源": station_cost_source,
        })
    grouped = pd.DataFrame(grouped_rows)
    if grouped.empty:
        return grouped, pd.DataFrame()

    output = []
    for (origin, route, batch), batch_group in grouped.groupby(["发货仓", "线路", "批次号"], sort=False):
        total_volume = float(batch_group["方数"].sum())
        station_cost_complete = (batch_group["明细仓点成本"] > 0).all() and len(batch_group) > 0
        embedded_batch_cost = _positive(batch_costs.get(batch, 0))
        fallback = stage2_costs.get(batch, {})
        stage2_cost = _positive(fallback.get("成本", 0))
        if station_cost_complete:
            basis = _joined_unique(batch_group["明细成本来源"])
            batch_cost = float(batch_group["明细仓点成本"].sum())
        elif embedded_batch_cost > 0:
            basis = "调拨明细批次总成本按方数分摊"
            batch_cost = embedded_batch_cost
        elif stage2_cost > 0:
            basis = "派送二批次成本按方数分摊"
            batch_cost = stage2_cost
        else:
            basis = "缺失"
            batch_cost = 0.0
        for _, item in batch_group.iterrows():
            volume = _positive(item["方数"])
            if station_cost_complete:
                allocated_cost = _positive(item["明细仓点成本"])
            else:
                allocated_cost = batch_cost * volume / total_volume if batch_cost > 0 and total_volume > 0 else 0.0
            output.append({
                "发货仓": origin,
                "FBA仓点": _text(item["FBA仓点"]),
                "平台": _text(item["平台"]),
                "是否FBA": bool(item["是否FBA"]),
                "来源": "调拨",
                "派送方式": "调拨",
                "批次号": batch,
                "线路": route,
                "供应商": _text(fallback.get("供应商", "")),
                "方数": volume,
                "成本": allocated_cost,
                "成本有效方数": volume if allocated_cost > 0 else 0.0,
                "成本阶段": "调拨干线",
                "成本来源": basis,
                "成本完整性": "缺二段末端",
            })
    all_transfer = pd.DataFrame(output)
    fba = all_transfer[(all_transfer["是否FBA"]) & (all_transfer["FBA仓点"] != "") & (all_transfer["方数"] > 0)].copy()
    return fba.drop(columns=["平台", "是否FBA"], errors="ignore"), all_transfer


def _safe_divide(numerator, denominator):
    return float(numerator) / float(denominator) if float(denominator) > 0 else pd.NA


def _aggregate_station(facts):
    rows = []
    if facts.empty:
        return pd.DataFrame()
    for (origin, station), group in facts.groupby(["发货仓", "FBA仓点"], sort=False):
        direct = group[group["来源"] == "直发"]
        transfer = group[group["来源"] == "调拨"]
        direct_volume = float(direct["方数"].sum())
        transfer_volume = float(transfer["方数"].sum())
        total_volume = direct_volume + transfer_volume
        direct_cost = float(direct["成本"].sum())
        transfer_cost = float(transfer["成本"].sum())
        direct_cost_volume = float(direct["成本有效方数"].sum())
        transfer_cost_volume = float(transfer["成本有效方数"].sum())
        valid_volume = direct_cost_volume + transfer_cost_volume
        rows.append({
            "发货仓": origin,
            "FBA仓点": station,
            "直发方数": direct_volume,
            "调拨方数": transfer_volume,
            "总方数": total_volume,
            "调拨占比": _safe_divide(transfer_volume, total_volume),
            "直发派送成本": direct_cost,
            "直发成本有效方数": direct_cost_volume,
            "直发成本/方": _safe_divide(direct_cost, direct_cost_volume),
            "调拨成本": transfer_cost,
            "调拨成本有效方数": transfer_cost_volume,
            "调拨成本/方": _safe_divide(transfer_cost, transfer_cost_volume),
            "调拨成本覆盖率": _safe_divide(transfer_cost_volume, transfer_volume),
            "总运输成本": direct_cost + transfer_cost,
            "成本有效方数": valid_volume,
            "真实每方成本": _safe_divide(direct_cost + transfer_cost, valid_volume),
            "真实成本覆盖率": _safe_divide(valid_volume, total_volume),
            "成本完整性": "缺二段末端" if transfer_volume > 0 else "直发口径",
            "直发批次数": direct["批次号"].replace("", pd.NA).nunique(),
            "调拨批次数": transfer["批次号"].replace("", pd.NA).nunique(),
            "直发线路": _joined_unique(direct["线路"]),
            "调拨线路": _joined_unique(transfer["线路"]),
        })
    result = pd.DataFrame(rows).sort_values(["发货仓", "总方数", "FBA仓点"], ascending=[True, False, True]).reset_index(drop=True)
    result["货量排名"] = result.groupby("发货仓")["总方数"].rank(method="first", ascending=False).astype(int)
    return result[["货量排名"] + [col for col in result.columns if col != "货量排名"]]


def _aggregate_method(facts, station_summary):
    rows = []
    station_cost = {(row["发货仓"], row["FBA仓点"]): row["真实每方成本"] for _, row in station_summary.iterrows()}
    station_volume = facts.groupby(["发货仓", "FBA仓点"])["方数"].sum().to_dict()
    for key, group in facts.groupby(["发货仓", "FBA仓点", "来源", "派送方式"], sort=False):
        origin, station, source, method = key
        volume = float(group["方数"].sum())
        cost = float(group["成本"].sum())
        valid_volume = float(group["成本有效方数"].sum())
        rows.append({
            "发货仓": origin,
            "FBA仓点": station,
            "来源": source,
            "派送方式": method,
            "方数": volume,
            "占仓点总货量": _safe_divide(volume, station_volume.get((origin, station), 0)),
            "成本阶段": _joined_unique(group["成本阶段"]),
            "成本": cost,
            "成本有效方数": valid_volume,
            "成本覆盖率": _safe_divide(valid_volume, volume),
            "方式每方成本": _safe_divide(cost, valid_volume),
            "仓点真实每方成本": station_cost.get((origin, station), pd.NA),
            "成本完整性": _joined_unique(group["成本完整性"]),
            "批次数": group["批次号"].replace("", pd.NA).nunique(),
            "线路": _joined_unique(group["线路"]),
            "供应商": _joined_unique(group["供应商"]),
        })
    return pd.DataFrame(rows).sort_values(["发货仓", "FBA仓点", "来源", "方数"], ascending=[True, True, True, False]).reset_index(drop=True)


def _build_checks(direct, transfer, all_transfer, station, methods, period_value="未核验", period_note=""):
    rows = []
    transfer_source_volume = float(all_transfer["方数"].sum()) if not all_transfer.empty else 0.0
    transfer_fba_volume = float(transfer["方数"].sum()) if not transfer.empty else 0.0
    non_fba_volume = transfer_source_volume - transfer_fba_volume
    facts = pd.concat([direct, transfer], ignore_index=True, sort=False)
    checks = [
        ("文件月份一致性", period_value, period_note),
        ("直发FBA方数", float(direct["方数"].sum()) if not direct.empty else 0.0, "派送二仓点分配明细中的有效直发FBA货量"),
        ("调拨全部方数", transfer_source_volume, "所选发货仓调拨SO明细全部平台货量"),
        ("调拨FBA方数", transfer_fba_volume, "调拨SO明细中Amazon/FBA平台货量"),
        ("调拨非FBA方数", non_fba_volume, "保留核对，不计入FBA仓点结果"),
        ("派送三FBA总方数", float(facts["方数"].sum()) if not facts.empty else 0.0, "直发FBA方数+调拨FBA方数"),
        ("仓点总览方数", float(station["总方数"].sum()) if not station.empty else 0.0, "应与派送三FBA总方数一致"),
        ("派送方式方数", float(methods["方数"].sum()) if not methods.empty else 0.0, "应与派送三FBA总方数一致"),
        ("调拨成本有效方数", float(transfer["成本有效方数"].sum()) if not transfer.empty else 0.0, "有正数调拨成本的FBA货量"),
        ("调拨成本覆盖率", _safe_divide(float(transfer["成本有效方数"].sum()) if not transfer.empty else 0.0, transfer_fba_volume), "成本有效调拨方数/调拨FBA方数"),
    ]
    for metric, value, note in checks:
        rows.append({"核对指标": metric, "数值": value, "说明": note})
    return pd.DataFrame(rows)


def _non_fba_summary(all_transfer):
    if all_transfer.empty:
        return pd.DataFrame(columns=["发货仓", "平台", "仓点", "方数", "成本", "批次数", "说明"])
    work = all_transfer[~all_transfer["是否FBA"]].copy()
    if work.empty:
        return pd.DataFrame(columns=["发货仓", "平台", "仓点", "方数", "成本", "批次数", "说明"])
    rows = []
    for key, group in work.groupby(["发货仓", "平台", "FBA仓点"], dropna=False, sort=False):
        origin, platform, station = key
        rows.append({
            "发货仓": origin,
            "平台": platform,
            "仓点": station,
            "方数": float(group["方数"].sum()),
            "成本": float(group["成本"].sum()),
            "批次数": group["批次号"].replace("", pd.NA).nunique(),
            "说明": "非Amazon/FBA调拨货量，仅供核对，未计入派送三FBA仓点结果",
        })
    return pd.DataFrame(rows).sort_values("方数", ascending=False).reset_index(drop=True)


def build_delivery3_reports(stage2_file, transfer_file, warehouse="全部"):
    warehouse = _normalize_origin(warehouse) if warehouse != "全部" else "全部"
    stage2 = _read_sheet(stage2_file, STAGE2_DETAIL_SHEET)
    transfer = _read_matching_sheet(
        transfer_file,
        TRANSFER_DETAIL_SHEET_ALIASES,
        ["调拨线路", "出库批次号", "仓点", "平台", "方数"],
        "调拨SO明细工作表",
        column_aliases=TRANSFER_DETAIL_COLUMN_ALIASES,
    )
    transfer_batches = _read_matching_sheet(
        transfer_file,
        TRANSFER_BATCH_SHEET_ALIASES,
        ["出库批次号"],
        "调拨批次汇总工作表",
        optional=True,
    )
    period_value, period_note = _validate_source_periods(stage2, transfer)
    direct = build_direct_facts(stage2, warehouse)
    transfer_fba, all_transfer = build_transfer_facts(transfer, stage2, transfer_batches, warehouse)
    facts = pd.concat([direct, transfer_fba], ignore_index=True, sort=False)
    if facts.empty:
        raise ValueError("没有识别到可用于派送三的直发或调拨FBA货量。请检查仓点选择、文件月份和字段内容。")
    station = _aggregate_station(facts)
    methods = _aggregate_method(facts, station)
    detail_columns = ["发货仓", "FBA仓点", "来源", "派送方式", "批次号", "线路", "供应商", "方数", "成本", "成本有效方数", "成本阶段", "成本来源", "成本完整性"]
    detail = facts[detail_columns].copy().sort_values(["发货仓", "FBA仓点", "来源", "批次号"]).reset_index(drop=True)
    checks = _build_checks(
        direct, transfer_fba, all_transfer, station, methods,
        period_value=period_value, period_note=period_note,
    )
    return {
        "派送三_仓点总览": station,
        "派送三_派送方式": methods,
        "派送三_批次明细": detail,
        "派送三_核对": checks,
        "调拨非FBA核对": _non_fba_summary(all_transfer),
    }

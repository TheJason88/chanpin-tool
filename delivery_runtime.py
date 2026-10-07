import re

import pandas as pd

import processors
import tool_common
import delivery_match_adapter
import delivery_stage1_adapter


RUNTIME_SCHEMA_VERSION = "2026-10-07-grain-audit-v34"
ORIGINAL_FILE_PERIOD = "按原文件时间范围"
TRANSFER_TARGETS = {
    "IL": {"name": "IL合作仓"},
    "LA": {"name": "LA盈仓"},
    "NJ": {"name": "NJ盈仓"},
    "SAV": {"name": "SAV盈仓"},
    "DAL": {"name": "DAL盈仓"},
}

# 原始代码已有：取消、作废、废单、无效、删除、关闭。这里补足历史备注删除关键词和新增关键词。
ADDITIONAL_INVALID_BATCH_KEYWORDS = ["废单", "公共单", "清除", "自提"]

# 明细阶段的LTL优先识别词；车次合并后的最终运输类型仍由
# apply_trip_transport_type_rules只按真实车次及原始运输证据判定，不用承运商名称推断FTL/LTL。
LTL_PRIORITY_KEYWORDS = ["LTL", "散货", "散板"]
LTL_REMARK_COLUMNS = ["备注", "备注信息", "MEMO", "跟进记录", "内部备注", "派送区域"]
START_TIME_CANDIDATES = ["批次出库时间", "出库时间", "实际出库时间"]
END_TIME_CANDIDATES = ["批次签收时间", "签收时间", "实际签收时间", "送达时间", "妥投时间"]


def _sync_common_rules():
    # 统一字段别名和调拨仓规则，避免多个补丁模块各自维护一套。
    delivery_stage1_adapter.VOLUME_CANDIDATES = tool_common.FIELD_ALIASES["出库体积"]
    delivery_stage1_adapter.PALLET_CANDIDATES = tool_common.FIELD_ALIASES["出库卡板数"]
    delivery_stage1_adapter.COST_CANDIDATES = tool_common.FIELD_ALIASES["派送成本"]
    delivery_stage1_adapter.TRANSFER_WAREHOUSE_INFO = tool_common.TRANSFER_WAREHOUSE_INFO
    delivery_match_adapter.TRANSFER_WAREHOUSE_INFO = tool_common.TRANSFER_WAREHOUSE_INFO
    delivery_match_adapter.INTEGER_COLUMNS = tool_common.INTEGER_OUTPUT_COLUMNS
    delivery_match_adapter.DECIMAL_COLUMNS = tool_common.DECIMAL_OUTPUT_COLUMNS


def _is_blank_value(value):
    try:
        return value is None or pd.isna(value)
    except Exception:
        return value is None


def _contains_any_keyword(value, keywords):
    if _is_blank_value(value):
        return False
    text = str(value)
    upper_text = text.upper()
    for keyword in keywords:
        k = str(keyword)
        if k and k.upper() in upper_text:
            return True
    return False


def _row_text(row, columns):
    values = []
    for col in columns:
        if col in row.index and not _is_blank_value(row.get(col, "")):
            values.append(str(row.get(col, "")))
    return " ".join(values)


def _find_existing_col(df, candidates):
    for col in candidates:
        if col in df.columns:
            return col
    return None


def _standardize_warehouse_value(value):
    try:
        return processors.standardize_warehouse(value)
    except Exception:
        text = str(value).upper().strip()
        if "LA" in text or "美西" in text or "CA" == text:
            return "LA"
        if "NJ" in text or "新泽西" in text:
            return "NJ"
        if "SAV" in text or "萨凡纳" in text:
            return "SAV"
        if "DAL" in text or "达拉斯" in text:
            return "DAL"
        return text


def _is_ltl_series(df):
    if df is None or df.empty:
        return pd.Series(False, index=getattr(df, "index", []))
    if "标准运输类型" in df.columns:
        return df["标准运输类型"].astype(str).str.strip().str.upper().eq("LTL")
    mask = pd.Series(False, index=df.index)
    for col in ["标准运输类型", "运输类型", "运输方式", "派送方式", "装车类型标准值"]:
        if col not in df.columns:
            continue
        text = df[col].astype(str).str.upper()
        mask = mask | text.str.contains("LTL", na=False) | text.str.contains("散货|散板", na=False)
    return mask


def _delivery_time_threshold_for_row(row):
    """
    分区域派送时效异常阈值。仅当行里已经有派送区域时启用；否则沿用通用30天清洗。
    规则里的“超过”按严格大于处理。
    """
    warehouse = _standardize_warehouse_value(row.get("仓库", ""))
    region = str(row.get("派送区域", row.get("区域", ""))).strip()
    region_upper = region.upper()

    if "LOCAL" in region_upper or "本地" in region or region == "Local":
        return 3.0

    if warehouse == "LA":
        if "中短途" in region:
            return 7.0
        if "美中" in region:
            return 10.0
        if "美东" in region or "美南" in region:
            return 15.0
        return 30.0

    if warehouse in ["NJ", "SAV", "DAL"]:
        if "中距离" in region:
            return 6.0
        if "远距离" in region:
            return 10.0
        return 30.0

    return 30.0


def _delivery_time_threshold_series(df):
    if df is None or df.empty:
        return pd.Series(dtype="float64", index=getattr(df, "index", []))
    if "派送区域" not in df.columns and "区域" not in df.columns:
        return pd.Series(30.0, index=df.index)
    return df.apply(_delivery_time_threshold_for_row, axis=1).astype(float)


def _clean_delivery_time_columns(df):
    """
    派送时效清洗口径：
    - 出库时间/签收时间任一缺失，派送时效留空，不再显示0；
    - 派送时效<=0视为无效，留空；
    - LTL按出库至签收重新计算，兼容旧版清洗文件中的空白时效；
    - 有派送区域时，按区域阈值清洗：
      Local>3天；LA中短途>7天、LA美中>10天、LA美东/美南>15天；
      NJ/SAV/DAL中距离>6天、远距离>10天；
    - 没有派送区域时，继续使用>30天兜底阈值；
    - 是否有效时效同步改为布尔口径，供后续均值/P80自然排除无效行。
    """
    if df is None or df.empty or "派送时效" not in df.columns:
        return df

    out = df.copy()
    start_col = _find_existing_col(out, START_TIME_CANDIDATES)
    end_col = _find_existing_col(out, END_TIME_CANDIDATES)

    duration = pd.to_numeric(out["派送时效"], errors="coerce")
    start_time = pd.to_datetime(out[start_col], errors="coerce") if start_col else None
    end_time = pd.to_datetime(out[end_col], errors="coerce") if end_col else None
    if start_time is not None and end_time is not None:
        ltl_duration = (end_time - start_time).dt.total_seconds() / 86400
        duration = duration.where(~_is_ltl_series(out), ltl_duration)
    thresholds = _delivery_time_threshold_series(out)
    invalid = duration.isna() | (duration <= 0) | duration.gt(thresholds)

    if start_col:
        invalid = invalid | start_time.isna()
    if end_col:
        invalid = invalid | end_time.isna()

    out["派送时效"] = duration.mask(invalid)
    if "是否有效时效" in out.columns:
        out["是否有效时效"] = ~invalid
    return out


def _patch_invalid_batch_keywords(delivery_workflow_module):
    """派送一无效批次剔除关键词补充。"""
    keywords = [
        keyword for keyword in getattr(delivery_workflow_module, "INVALID_BATCH_KEYWORDS", [])
        if keyword != "快递"
    ]
    for keyword in ADDITIONAL_INVALID_BATCH_KEYWORDS:
        if keyword not in keywords:
            keywords.append(keyword)
    delivery_workflow_module.INVALID_BATCH_KEYWORDS = keywords
    return delivery_workflow_module


def _set_text_for_mask(df, mask, col, value, create=False):
    """安全写入文本值，避免 pandas 3 对 int/float 列写入字符串时报 dtype 错。"""
    if col not in df.columns:
        if not create:
            return
        df[col] = pd.Series([pd.NA] * len(df), index=df.index, dtype="object")
    elif str(df[col].dtype) != "object":
        df[col] = df[col].astype("object")
    df.loc[mask, col] = value


def _apply_ltl_priority_to_detail(detail_df):
    """备注含LTL时强制LTL；备注含快递时只覆盖派送方式为“转快递”。"""
    if detail_df is None or detail_df.empty:
        return detail_df

    out = detail_df.copy()
    remark_cols = [col for col in LTL_REMARK_COLUMNS if col in out.columns]
    if remark_cols:
        remark_text = out.apply(lambda row: _row_text(row, remark_cols), axis=1)
        ltl_mask = remark_text.map(
            lambda value: _contains_any_keyword(value, LTL_PRIORITY_KEYWORDS)
        )
        courier_mask = remark_text.map(
            lambda value: _contains_any_keyword(value, ["快递"])
        )

        if ltl_mask.any():
            _set_text_for_mask(out, ltl_mask, "标准运输类型", "LTL", create=True)
            _set_text_for_mask(out, ltl_mask, "运输类型", "LTL")
            _set_text_for_mask(out, ltl_mask, "运输方式", "LTL")
            _set_text_for_mask(out, ltl_mask, "派送方式", "散板出库")
            _set_text_for_mask(out, ltl_mask, "标准派送方式", "散板出库")
            _set_text_for_mask(out, ltl_mask, "车型标准值", "不适用")
            _set_text_for_mask(out, ltl_mask, "装车类型标准值", "散板")

        # LTL优先：同一备注同时出现“LTL”和“快递”时，仍按LTL，不改成转快递。
        courier_only = courier_mask & ~ltl_mask
        if courier_only.any():
            _set_text_for_mask(out, courier_only, "派送方式", "转快递")
            _set_text_for_mask(out, courier_only, "标准派送方式", "转快递")
    return _clean_delivery_time_columns(out)

def _patch_ltl_priority_from_remarks():
    """功能一原始明细清洗后、合并车次前，按备注优先纠正LTL。"""
    current_func = processors.process_delivery_stage1_from_files
    if getattr(current_func, "_ltl_remark_priority_v4", False):
        return

    original_func = getattr(processors, "_original_process_delivery_stage1_from_files", current_func)
    processors._original_process_delivery_stage1_from_files = original_func

    def process_delivery_stage1_from_files_with_ltl_priority(*args, **kwargs):
        result = original_func(*args, **kwargs)
        if isinstance(result, tuple) and len(result) >= 1:
            detail_df = _apply_ltl_priority_to_detail(result[0])
            return (detail_df,) + tuple(result[1:])
        return result

    process_delivery_stage1_from_files_with_ltl_priority._ltl_remark_priority_v4 = True
    processors.process_delivery_stage1_from_files = process_delivery_stage1_from_files_with_ltl_priority


def _normalize_batch_key(value):
    if pd.isna(value):
        return ""
    text = str(value).strip()
    if text.lower() in ["nan", "none", "null", "<na>"]:
        return ""
    # Excel 有时会把纯数字批次号读成 12345.0，这里统一还原，避免匹配不到。
    if re.fullmatch(r"\d+\.0", text):
        return text[:-2]
    return text


def _aggregate_unique_batch_costs(cost_values):
    """
    一车多批次派送成本口径：
    - 先取每个批次的首个有效成本；
    - 多个批次里相同成本只保留一次；
    - 不同成本相加；
    - 不再执行超过 12000 留空规则。
    """
    values = pd.to_numeric(pd.Series(cost_values), errors="coerce").dropna().astype(float)
    if values.empty:
        return 0.0

    total = 0.0
    seen = set()
    for value in values:
        key = round(float(value), 6)
        if key in seen:
            continue
        seen.add(key)
        total += float(value)
    return total


def _batch_costs_by_ordered_batch_ids(detail, batch_keys):
    batch_costs = []
    for batch_key in batch_keys:
        values = detail.loc[detail["批次号_匹配Key"] == batch_key, "派送成本"].dropna()
        if not values.empty:
            # 同一批次在原始明细中可能出现多行，派送成本按该批次首个有效成本取一次。
            batch_costs.append(float(values.iloc[0]))
    return batch_costs


def _stage1_force_totals_with_unique_cost_rule(cleaned_batches, detail_df):
    """
    替换 delivery_stage1_adapter 原来的强制回填逻辑。
    只改变派送成本聚合口径，方数、板数、FBA/FBX方数仍沿用原有汇总方式。
    """
    if cleaned_batches is None or cleaned_batches.empty or detail_df is None or detail_df.empty:
        return cleaned_batches

    detail = _apply_ltl_priority_to_detail(detail_df)
    detail = delivery_stage1_adapter.repair_delivery_stage1_numeric_columns(detail)
    detail = delivery_stage1_adapter._ensure_numeric(detail, delivery_stage1_adapter.NUMERIC_COLS)
    if "批次号" not in detail.columns:
        return _clean_delivery_time_columns(cleaned_batches)
    if "FBA/FBX" not in detail.columns:
        detail["FBA/FBX"] = ""
    detail["批次号_匹配Key"] = detail["批次号"].apply(_normalize_batch_key)

    out = delivery_stage1_adapter._prepare_recalc_columns(cleaned_batches)

    for idx, row in out.iterrows():
        batch_ids = delivery_stage1_adapter._split_batch_ids(row.get("批次号集合", row.get("批次号", "")))
        batch_keys = [_normalize_batch_key(x) for x in batch_ids]
        batch_keys = [x for x in batch_keys if x]
        if not batch_keys:
            continue

        matched = detail[detail["批次号_匹配Key"].isin(batch_keys)].copy()
        if matched.empty:
            continue

        out.at[idx, "出库体积"] = float(matched["出库体积"].sum())
        out.at[idx, "出库卡板数"] = float(matched["出库卡板数"].sum())
        base_cost = _aggregate_unique_batch_costs(_batch_costs_by_ordered_batch_ids(matched, batch_keys))
        out.at[idx, tool_common.BASE_DELIVERY_COST_COLUMN] = base_cost
        out.at[idx, "派送成本"] = base_cost
        out.at[idx, "FBA出库体积"] = float(matched.loc[matched["FBA/FBX"] == "FBA", "出库体积"].sum())
        out.at[idx, "FBX出库体积"] = float(matched.loc[matched["FBA/FBX"] == "FBX", "出库体积"].sum())

        # 主产品类型同步按方数重新判定。
        fba_volume = float(out.at[idx, "FBA出库体积"] or 0)
        fbx_volume = float(out.at[idx, "FBX出库体积"] or 0)
        if fba_volume > 0 and fbx_volume > 0:
            out.at[idx, "系统产品类型"] = "混合目的地"
        elif fba_volume > 0:
            out.at[idx, "系统产品类型"] = "FBA"
        elif fbx_volume > 0:
            out.at[idx, "系统产品类型"] = "FBX"
        out.at[idx, "主产品类型"] = "FBA" if fba_volume >= fbx_volume and fba_volume > 0 else ("FBX" if fbx_volume > 0 else "未知")

    return _clean_delivery_time_columns(tool_common.apply_floor_loading_fee(out))


def _apply_trip_cost_rule(cleaned_batches, raw_detail):
    """兜底回填派送成本，确保功能一最终输出仍使用同一套成本口径。"""
    if cleaned_batches is None or cleaned_batches.empty or raw_detail is None or raw_detail.empty:
        return _clean_delivery_time_columns(cleaned_batches)
    if "批次号" not in raw_detail.columns or "派送成本" not in raw_detail.columns:
        return _clean_delivery_time_columns(cleaned_batches)

    detail = _apply_ltl_priority_to_detail(raw_detail).copy()
    detail["批次号_匹配Key"] = detail["批次号"].apply(_normalize_batch_key)
    detail["派送成本"] = pd.to_numeric(detail["派送成本"], errors="coerce")

    out = cleaned_batches.copy()
    if "派送成本" not in out.columns:
        out["派送成本"] = pd.NA

    for idx, row in out.iterrows():
        batch_ids = delivery_stage1_adapter._split_batch_ids(row.get("批次号集合", row.get("批次号", "")))
        batch_keys = [_normalize_batch_key(x) for x in batch_ids]
        batch_keys = [x for x in batch_keys if x]
        if not batch_keys:
            continue

        matched = detail[detail["批次号_匹配Key"].isin(batch_keys)].copy()
        if matched.empty:
            continue
        base_cost = _aggregate_unique_batch_costs(_batch_costs_by_ordered_batch_ids(matched, batch_keys))
        out.at[idx, tool_common.BASE_DELIVERY_COST_COLUMN] = base_cost
        out.at[idx, "派送成本"] = base_cost

    return _clean_delivery_time_columns(tool_common.apply_floor_loading_fee(out))


def _original_file_period_label(df, date_col="批次出库时间"):
    if df is None or df.empty or date_col not in df.columns:
        return "原文件全部时间范围"
    valid_dates = pd.to_datetime(df[date_col], errors="coerce").dropna()
    if valid_dates.empty:
        return "原文件全部时间范围"
    return f"{valid_dates.min().strftime('%Y-%m-%d')} ~ {valid_dates.max().strftime('%Y-%m-%d')}"


def _patch_stage2_original_file_period(delivery_workflow_module):
    """派送二原文件范围：运营和成本统一按批次出库时间归期。"""
    current_func = delivery_workflow_module.add_analysis_period
    if getattr(current_func, "_supports_original_file_period", False):
        return delivery_workflow_module

    original_func = getattr(delivery_workflow_module, "_original_add_analysis_period", current_func)
    delivery_workflow_module._original_add_analysis_period = original_func

    def add_analysis_period_with_original_file_range(df, period_type):
        if period_type != ORIGINAL_FILE_PERIOD:
            return original_func(df, period_type)
        out = df.copy()
        out["批次出库时间"] = pd.to_datetime(out["批次出库时间"], errors="coerce")
        if "批次创建时间" not in out.columns:
            out["批次创建时间"] = pd.NaT
        out["批次创建时间"] = pd.to_datetime(out["批次创建时间"], errors="coerce")
        out["统计周期"] = _original_file_period_label(out, "批次出库时间")
        out["成本统计周期"] = out["统计周期"]
        return out

    add_analysis_period_with_original_file_range._supports_original_file_period = True
    delivery_workflow_module.add_analysis_period = add_analysis_period_with_original_file_range
    return delivery_workflow_module


def _patch_stage2_prepare_time_rules(delivery_workflow_module):
    """派送二恢复LTL日期时效，并排除缺日期、非正时效和区域超阈值。"""
    current_func = delivery_workflow_module.prepare_stage2_for_report
    if getattr(current_func, "_cleans_delivery_time_v2", False):
        return delivery_workflow_module

    original_func = getattr(delivery_workflow_module, "_original_prepare_stage2_for_report", current_func)
    delivery_workflow_module._original_prepare_stage2_for_report = original_func

    def prepare_stage2_for_report_with_clean_time(cleaned_batches, match_df, period_type):
        matched = original_func(cleaned_batches, match_df, period_type)
        return _clean_delivery_time_columns(matched)

    prepare_stage2_for_report_with_clean_time._cleans_delivery_time_v2 = True
    delivery_workflow_module.prepare_stage2_for_report = prepare_stage2_for_report_with_clean_time
    return delivery_workflow_module


def _unique_batch_keys_from_row(row):
    batch_ids = delivery_stage1_adapter._split_batch_ids(row.get("批次号集合", row.get("批次号", "")))
    batch_keys = [_normalize_batch_key(x) for x in batch_ids]
    return list(dict.fromkeys([x for x in batch_keys if x]))


def _transfer_target_from_row(row):
    """识别明确的调拨，包含 NJ 至 SAV 及 LA 至指定 IL 合作仓。"""
    route = tool_common.transfer_route_from_row(row)
    return route.rsplit("-", 1)[-1] if route else ""


def _transfer_rows(matched, ftl_only=True, include_missing_trip=False):
    if matched is None or matched.empty:
        return pd.DataFrame()
    out = matched.copy()
    out["调拨目标仓"] = out.apply(_transfer_target_from_row, axis=1)
    out = out[out["调拨目标仓"].isin(TRANSFER_TARGETS.keys())].copy()
    if ftl_only:
        if include_missing_trip and "标准运输类型" in out.columns:
            out = out[out["标准运输类型"].astype(str).str.upper().eq("FTL")].copy()
        elif "是否FTL发车" in out.columns:
            out = out[tool_common.normalize_boolean_series(out["是否FTL发车"])].copy()
    for col in ["出库体积", "出库卡板数", "派送成本"]:
        if col not in out.columns:
            out[col] = 0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0)
    out["调拨目标仓名称"] = out["调拨目标仓"].map(lambda x: TRANSFER_TARGETS.get(x, {}).get("name", x))
    out["仓库"] = out["仓库"].map(processors.standardize_warehouse)
    out["专线线路"] = pd.Series(
        [tool_common.transfer_route(source, target) for source, target in zip(out["仓库"], out["调拨目标仓"])],
        index=out.index, dtype="object",
    )
    return out


def _filter_positive_cost_rows(df):
    """成本测算只纳入原始派送成本大于0的批次；追加费用不能把0成本批次转为样本。"""
    if df is None or df.empty:
        return df
    out = df.copy()
    if "派送成本" not in out.columns and tool_common.BASE_DELIVERY_COST_COLUMN not in out.columns:
        return out.iloc[0:0].copy()
    cost_source = out.get(tool_common.BASE_DELIVERY_COST_COLUMN, out.get("派送成本"))
    cost_source = pd.to_numeric(cost_source, errors="coerce").fillna(0)
    if "派送成本" in out.columns:
        out["派送成本"] = pd.to_numeric(out["派送成本"], errors="coerce").fillna(0)
    return out[cost_source > 0].copy()


def _business_round_vehicle_count(value):
    value = pd.to_numeric(value, errors="coerce")
    if pd.isna(value) or float(value) <= 0:
        return 0
    return int(float(value) + 0.5)


def _exact_vehicle_share_series(df):
    if "批次车份额" in df.columns:
        share = pd.to_numeric(df["批次车份额"], errors="coerce")
        batch_count = pd.to_numeric(
            df.get("整车批次数", pd.Series(1, index=df.index)), errors="coerce",
        ).fillna(1)
        return share.fillna(batch_count.le(1).astype(float)).clip(lower=0, upper=1)
    # 兼容旧版一行一整车的派送一结果。
    return pd.Series(1.0, index=df.index)


def _transfer_vehicle_scope(group):
    """Return exact vehicle share and rows belonging to full transfer vehicles.

    A mixed-destination truck contributes only its batch share to the transfer
    vehicle total and never enters per-truck averages.  Multiple transfer
    batches that together cover the complete truck remain one full vehicle.
    """
    if group is None or group.empty:
        return 0.0, group.copy(), 0, 0.0
    out = group.copy()
    warehouse = out.get("仓库", pd.Series("", index=out.index)).fillna("").astype(str).str.upper().str.strip()
    trip_no = out.get("车次号", pd.Series("", index=out.index)).fillna("").astype(str).str.strip()
    valid = trip_no.ne("")
    if "是否有真实车次号" in out.columns:
        valid &= tool_common.normalize_boolean_series(out["是否有真实车次号"])
    out = out.loc[valid].copy()
    if out.empty:
        return 0.0, out, 0, 0.0
    out["_调拨车次键"] = warehouse.loc[out.index] + "||" + trip_no.loc[out.index]
    out["_调拨车份额"] = _exact_vehicle_share_series(out)
    trip_shares = out.groupby("_调拨车次键", sort=False)["_调拨车份额"].sum().clip(upper=1)
    exact_share = float(trip_shares.sum())
    full_keys = set(trip_shares[trip_shares.ge(1 - 1e-6)].index)
    full_rows = out[out["_调拨车次键"].isin(full_keys)].copy()
    full_count = len(full_keys)
    partial_share = max(0.0, exact_share - float(full_count))
    return exact_share, full_rows, full_count, partial_share


def _station_cost_source_rows(matched):
    """仓点成本从完整清洗后数据独立取数，不受干线/调拨标签反向排除。"""
    if matched is None:
        return matched
    return matched.copy()


# 兼容旧测试或外部调用；旧函数名不再代表排除干线/调拨。
_filter_regular_trips_for_cost = _station_cost_source_rows


def _filter_regular_single_batch_trips_for_cost(matched):
    marked = processors.mark_whole_truck_cost_sample_eligibility(matched)
    return processors.whole_truck_cost_sample_rows(marked)


def _combine_series_text(series):
    if series is None:
        return ""
    values = []
    for value in series:
        if pd.isna(value):
            continue
        text = str(value).strip()
        if text and text.lower() not in ["nan", "none", "null", "<na>"] and text not in values:
            values.append(text)
    return ",".join(values)


def _build_transfer_cost_report(matched):
    marked = processors.mark_whole_truck_cost_sample_eligibility(matched)
    transfer = _filter_positive_cost_rows(_transfer_rows(marked, ftl_only=True))
    columns = [
        "指标名称", "仓库", "统计周期", "对象类型", "平台", "仓点代码", "车型装车分组",
        "车次数", "完整调拨车次数", "混合目的地折算车份额", "总出库体积", "总派送成本",
        "每方调拨成本（总成本÷总方数）", "平均整车价", "P80整车价", "P90整车价", "每方平均价", "P80每方平均价", "P90每方平均价",
        "平均每车出库体积", "P80每车出库体积", "P90每车出库体积", "平均整车价有效车次数",
        "平均每方价有效批次数", "平均装载有效车次数", "平均装载口径", "车次口径",
    ]
    if transfer.empty:
        return pd.DataFrame(columns=columns)
    if "成本统计周期" in transfer.columns:
        transfer["统计周期"] = transfer["成本统计周期"].fillna("未知周期")

    rows = []
    for (warehouse, period, target, target_name), group in transfer.groupby(["仓库", "统计周期", "调拨目标仓", "调拨目标仓名称"], dropna=False):
        exact_trip_share, full_transfer_rows, full_trip_count, partial_share = _transfer_vehicle_scope(group)
        total_volume = group["出库体积"].sum()
        total_cost = group["派送成本"].sum()
        # Batch-level unit-price averages may use valid allocated transfer costs.
        # Whole-truck price/load samples are filtered separately below.
        average_source = group.copy()
        if "整车出库体积" in average_source.columns:
            average_source["批次出库体积"] = average_source["出库体积"]
            average_source["出库体积"] = pd.to_numeric(average_source["整车出库体积"], errors="coerce")
        average_group = processors.average_sample_rows(average_source)
        cost_group = processors.whole_truck_cost_sample_rows(average_group)
        whole_truck_prices = pd.to_numeric(cost_group["派送成本"], errors="coerce").dropna()
        whole_truck_prices = whole_truck_prices[whole_truck_prices.gt(0)]
        denominator_col = "批次出库体积" if "批次出库体积" in average_group.columns else "出库体积"
        detail_prices = processors.detail_ratio_values(average_group, "派送成本", denominator_col)
        trip_loads = processors.full_trip_load_sample_rows(full_transfer_rows)
        rows.append({
            "指标名称": "调拨成本",
            "仓库": warehouse,
            "统计周期": period,
            "对象类型": "仓间调拨",
            "平台": "联宇盈仓",
            "仓点代码": target_name,
            "车型装车分组": "不区分车型",
            "车次数": round(exact_trip_share, 2),
            "完整调拨车次数": int(full_trip_count),
            "混合目的地折算车份额": round(partial_share, 2),
            "总出库体积": total_volume,
            "总派送成本": total_cost,
            "每方调拨成本（总成本÷总方数）": processors.safe_divide(total_cost, total_volume),
            "平均整车价": whole_truck_prices.mean() if not whole_truck_prices.empty else pd.NA,
            "P80整车价": processors.safe_p80(whole_truck_prices),
            "P90整车价": processors.safe_p90(whole_truck_prices),
            "每方平均价": detail_prices.mean() if not detail_prices.empty else pd.NA,
            "P80每方平均价": processors.safe_p80(detail_prices),
            "P90每方平均价": processors.safe_p90(detail_prices),
            "平均每车出库体积": pd.to_numeric(trip_loads.get("完整车次出库体积"), errors="coerce").mean(),
            "P80每车出库体积": processors.safe_p80(
                trip_loads.get("完整车次出库体积", pd.Series(dtype=float))
            ),
            "P90每车出库体积": processors.safe_p90(
                trip_loads.get("完整车次出库体积", pd.Series(dtype=float))
            ),
            "平均整车价有效车次数": int(len(whole_truck_prices)),
            "平均每方价有效批次数": int(len(detail_prices)),
            "平均装载有效车次数": int(len(trip_loads)),
            "平均装载口径": "按真实车次去重后取完整整车方数的有效样本算术平均",
            "车次口径": "完整调拨车计1；混合目的地车仅计调拨批次的精确车份额",
        })
    return pd.DataFrame(rows)[columns]


def _build_transfer_report(matched):
    marked = processors.mark_whole_truck_cost_sample_eligibility(matched)
    # 调拨总方数、总成本和每方调拨成本必须共用同一批有效调拨卸点：
    # 仅保留原始派送成本>0的调拨批次，避免零成本批次进入方数分母。
    transfer = _filter_positive_cost_rows(
        _transfer_rows(marked, ftl_only=True, include_missing_trip=True)
    )
    columns = [
        "发货仓", "调拨目标仓", "专线线路", "统计周期", "车次数", "完整调拨车次数",
        "混合目的地折算车份额", "总出库体积", "总出库卡板数", "总派送成本",
        "每方调拨成本（总成本÷总方数）", "平均整车价", "P80整车价", "P90整车价", "每方平均价", "P80每方平均价", "P90每方平均价",
        "平均每车出库体积", "P80每车出库体积", "P90每车出库体积", "供应商平均整车价", "供应商平均整车成本", "供应商使用比例",
        "平均整车价有效车次数", "平均每方价有效批次数", "平均装载有效车次数", "平均装载口径", "车次口径",
    ]
    if transfer.empty:
        return pd.DataFrame(columns=columns)
    if "成本统计周期" in transfer.columns:
        transfer["统计周期"] = transfer["成本统计周期"].fillna("未知周期")

    rows = []
    for (warehouse, period, target_name, line), group in transfer.groupby(["仓库", "统计周期", "调拨目标仓名称", "专线线路"], dropna=False):
        if "是否FTL发车" in group.columns:
            dispatched = group[tool_common.normalize_boolean_series(group["是否FTL发车"])].copy()
        else:
            dispatched = group[group["车次号"].fillna("").astype(str).str.strip().ne("")].copy()
        exact_trip_share, full_transfer_rows, full_trip_count, partial_share = _transfer_vehicle_scope(dispatched)
        total_volume = group["出库体积"].sum()
        total_pallets = group["出库卡板数"].sum()
        total_cost = group["派送成本"].sum()
        # Keep valid allocated mixed-destination batches for per-CBM price metrics;
        # whole-truck price eligibility and load samples remain full-transfer-only.
        average_source = _filter_positive_cost_rows(dispatched)
        if "整车出库体积" in average_source.columns:
            average_source["批次出库体积"] = average_source["出库体积"]
            average_source["出库体积"] = pd.to_numeric(average_source["整车出库体积"], errors="coerce")
        average_group = processors.average_sample_rows(average_source)
        cost_group = processors.whole_truck_cost_sample_rows(average_group)
        whole_truck_prices = pd.to_numeric(cost_group["派送成本"], errors="coerce").dropna()
        whole_truck_prices = whole_truck_prices[whole_truck_prices.gt(0)]
        denominator_col = "批次出库体积" if "批次出库体积" in average_group.columns else "出库体积"
        detail_prices = processors.detail_ratio_values(average_group, "派送成本", denominator_col)
        trip_loads = processors.full_trip_load_sample_rows(full_transfer_rows)
        supplier_costs, supplier_usage = processors.supplier_whole_truck_cost_summary(cost_group)
        rows.append({
            "发货仓": warehouse,
            "调拨目标仓": target_name,
            "专线线路": line,
            "统计周期": period,
            "车次数": round(exact_trip_share, 2),
            "完整调拨车次数": int(full_trip_count),
            "混合目的地折算车份额": round(partial_share, 2),
            "总出库体积": total_volume,
            "总出库卡板数": total_pallets,
            "总派送成本": total_cost,
            "每方调拨成本（总成本÷总方数）": processors.safe_divide(total_cost, total_volume),
            "平均整车价": whole_truck_prices.mean() if not whole_truck_prices.empty else pd.NA,
            "P80整车价": processors.safe_p80(whole_truck_prices),
            "P90整车价": processors.safe_p90(whole_truck_prices),
            "每方平均价": detail_prices.mean() if not detail_prices.empty else pd.NA,
            "P80每方平均价": processors.safe_p80(detail_prices),
            "P90每方平均价": processors.safe_p90(detail_prices),
            "平均每车出库体积": pd.to_numeric(trip_loads.get("完整车次出库体积"), errors="coerce").mean(),
            "P80每车出库体积": processors.safe_p80(trip_loads.get("完整车次出库体积", pd.Series(dtype=float))),
            "P90每车出库体积": processors.safe_p90(trip_loads.get("完整车次出库体积", pd.Series(dtype=float))),
            "供应商平均整车成本": supplier_costs,
            "供应商使用比例": supplier_usage,
            "供应商平均整车价": processors.supplier_whole_truck_average_cost(cost_group),
            "平均整车价有效车次数": int(len(whole_truck_prices)),
            "平均每方价有效批次数": int(len(detail_prices)),
            "平均装载有效车次数": int(len(trip_loads)),
            "平均装载口径": "按真实车次去重后取完整整车方数的有效样本算术平均",
            "车次口径": "完整调拨车计1；混合目的地车仅计调拨批次的精确车份额",
        })
    return pd.DataFrame(rows)[columns]


def _patch_cost_report_single_batch_only():
    """派送二成本表口径：普通派送只看单批次单车次且成本>0；调拨成本单独按目标盈仓汇总且成本>0。"""
    current_func = delivery_match_adapter.build_station_cost_report
    if getattr(current_func, "_single_batch_and_transfer_cost", False):
        return

    original_func = getattr(
        delivery_match_adapter,
        "_base_build_station_cost_report",
        getattr(delivery_match_adapter, "_original_build_station_cost_report", current_func),
    )
    delivery_match_adapter._base_build_station_cost_report = original_func
    delivery_match_adapter._original_build_station_cost_report = original_func

    def build_station_cost_report_with_transfer(matched):
        # 仓点成本、干线、调拨是从同一清洗后数据集并行取数的三个分析分支。
        # 干线/调拨标签只能用于各自汇总，不能从FBA/FBX仓点成本中剔除批次。
        station_cost = original_func(_station_cost_source_rows(matched))
        transfer_cost = _build_transfer_cost_report(matched)
        frames = [df for df in [station_cost, transfer_cost] if df is not None and not df.empty]
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True, sort=False)

    build_station_cost_report_with_transfer._single_batch_and_transfer_cost = True
    delivery_match_adapter.build_station_cost_report = build_station_cost_report_with_transfer


def _patch_stage2_transfer_sheet():
    """派送二结果增加“调拨数据”独立表。"""
    current_func = delivery_match_adapter.build_split_stage2_report
    if getattr(current_func, "_includes_transfer_sheet", False):
        return

    def build_split_stage2_report_with_transfer(delivery_workflow_module, cleaned_batches, match_df, period_type="按周统计"):
        import delivery_destination_analysis

        matched = delivery_workflow_module.prepare_stage2_for_report(cleaned_batches, match_df, period_type)
        matched = _clean_delivery_time_columns(matched)
        combined = delivery_workflow_module.build_sheet1_volume_dispatch_time_report(matched)
        if combined.empty:
            volume = dispatch = timing = combined.copy()
        else:
            volume = combined[combined["报告部分"].astype(str).str.startswith("1.")].copy()
            volume = volume[~volume["指标名称"].astype(str).isin(["FBA仓点货量排行", "FBX平台仓货量排行"])]
            dispatch = combined[combined["报告部分"].astype(str).str.startswith("2.")].copy()
            timing = combined[combined["报告部分"].astype(str).str.startswith("3.")].copy()

        timing = delivery_destination_analysis.build_station_timing_report(matched)
        fba_summary, fba_methods = delivery_destination_analysis.build_fba_destination_reports(matched)
        cost_ftl = delivery_match_adapter.build_station_cost_report(matched)
        cost_ltl = delivery_match_adapter.build_ltl_station_cost_report(matched)
        price_reference, type_price_reference = delivery_match_adapter.build_cost_price_reference_reports(
            cost_ftl,
            cost_ltl,
        )
        golden_standard = delivery_match_adapter.build_golden_standard_batch_report(matched)
        transfer_report = _build_transfer_report(matched)
        if "目的地邮编待补充" in matched.columns:
            zip_audit = matched[tool_common.normalize_boolean_series(matched["目的地邮编待补充"])].copy()
        else:
            zip_audit = pd.DataFrame()

        return {
            "FBA仓点分析": delivery_match_adapter._safe_round(fba_summary, "成本"),
            "FBA派送方式分析": delivery_match_adapter._safe_round(fba_methods, "成本"),
            "货量": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(volume, "货量"), "货量"),
            "FBA货量排行": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(delivery_match_adapter.build_fba_rank_sheet(matched), "FBA货量排行"), "FBA货量排行"),
            "FBX平台仓货量": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(delivery_match_adapter.build_fbx_platform_warehouse_sheet(matched), "FBX平台仓货量"), "FBX平台仓货量"),
            "发车量": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(dispatch, "发车量"), "发车量"),
            "派送时效": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(timing, "派送时效"), "派送时效"),
            "调拨数据": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(transfer_report, "调拨数据"), "调拨数据"),
            "每方价格参考": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(price_reference, "成本"), "成本"),
            "分类型价格参考": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(type_price_reference, "成本"), "成本"),
            "黄金标准数据": delivery_match_adapter._safe_round(golden_standard, "明细"),
            "派送二_匹配后批次数据": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(matched, "明细"), "明细"),
            "派送二_车次汇总核对": delivery_match_adapter._safe_round(delivery_workflow_module.build_trip_audit(matched), "明细"),
            "邮编异常审核": delivery_match_adapter._finalize_zip_audit_sheet(zip_audit),
            "区域识别规则": delivery_workflow_module.REGION_RULES_DF,
            "干线识别规则": delivery_workflow_module.LINEHAUL_RULES,
        }

    build_split_stage2_report_with_transfer._includes_transfer_sheet = True
    delivery_match_adapter.build_split_stage2_report = build_split_stage2_report_with_transfer
    # 供 app.py 的FBA/FBX专项报告后续复用；不影响普通调用。
    delivery_match_adapter.build_transfer_report = _build_transfer_report


def _wrap_stage1_no_time_filter_and_dominant_destination(delivery_workflow_module):
    if hasattr(delivery_workflow_module, "_unified_stage1_process_wrapped"):
        return delivery_workflow_module

    base_func = delivery_workflow_module.process_stage1_raw_files_to_cleaned_batches

    def unified_stage1_process(file_dfs, warehouse, period_type="不适用", start_date=None, end_date=None):
        # 功能一只负责全量清洗，不再按页面时间范围筛选。
        result = base_func(
            file_dfs=file_dfs,
            warehouse=warehouse,
            period_type=period_type,
            start_date=None,
            end_date=None,
        )
        if isinstance(result, tuple) and len(result) == 4:
            cleaned_batches, invalid_detail, zip_audit_df, raw_detail = result
            raw_detail = _apply_ltl_priority_to_detail(raw_detail)
            # 调拨只覆盖本批次目的地；同一车次的其他FBA/FBX批次保留各自目的地，
            # 以支持一车多卸。车次仍只提供运输类型、车型装车和批次车份额上下文。
            cleaned_batches = _apply_trip_cost_rule(cleaned_batches, raw_detail)
            cleaned_batches = _clean_delivery_time_columns(cleaned_batches)
            from delivery_destination_analysis import annotate_trip_context
            cleaned_batches = annotate_trip_context(cleaned_batches)
            if cleaned_batches is not None and not cleaned_batches.empty:
                if "备注" not in cleaned_batches.columns:
                    cleaned_batches["备注"] = ""
                cleaned_batches = cleaned_batches[[col for col in cleaned_batches.columns if col != "备注"] + ["备注"]]
            if cleaned_batches is not None and not cleaned_batches.empty and "目的地邮编待补充" in cleaned_batches.columns:
                zip_audit_df = cleaned_batches[tool_common.normalize_boolean_series(cleaned_batches["目的地邮编待补充"])].copy()
            return cleaned_batches, invalid_detail, zip_audit_df, raw_detail
        return result

    delivery_workflow_module.process_stage1_raw_files_to_cleaned_batches = unified_stage1_process
    delivery_workflow_module._unified_stage1_process_wrapped = True
    return delivery_workflow_module


def bootstrap(delivery_workflow_module):
    """集中应用派送运行时补丁，app.py只调用这一处，避免多处散落 patch。"""
    _sync_common_rules()
    _patch_invalid_batch_keywords(delivery_workflow_module)
    _patch_stage2_original_file_period(delivery_workflow_module)
    _patch_cost_report_single_batch_only()
    _patch_stage2_transfer_sheet()
    # 关键修正：把成本聚合规则挂到功能一强制回填函数本身，避免后置 wrapper 未生效时派送成本仍按明细简单相加。
    delivery_stage1_adapter._force_cleaned_totals_from_detail = _stage1_force_totals_with_unique_cost_rule
    delivery_match_adapter.patch_delivery_workflow(delivery_workflow_module)
    delivery_stage1_adapter.patch_delivery_stage1(delivery_workflow_module)
    _patch_ltl_priority_from_remarks()
    _patch_stage2_prepare_time_rules(delivery_workflow_module)
    _wrap_stage1_no_time_filter_and_dominant_destination(delivery_workflow_module)
    return delivery_workflow_module

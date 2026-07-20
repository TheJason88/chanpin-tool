import re

import pandas as pd

import processors
import tool_common
import delivery_match_adapter
import delivery_stage1_adapter


ORIGINAL_FILE_PERIOD = "按原文件时间范围"
TRANSFER_TARGETS = {
    "NJ": {"name": "NJ盈仓", "line": "LA-NJ"},
    "SAV": {"name": "SAV盈仓", "line": "LA-SAV"},
    "DAL": {"name": "DAL盈仓", "line": "LA-DAL"},
}
TRANSFER_KEYWORDS = ["调拨", "仓间", "调入"]

# 原始代码已有：取消、作废、废单、无效、删除、关闭。这里补足历史备注删除关键词和新增关键词。
ADDITIONAL_INVALID_BATCH_KEYWORDS = ["废单", "快递", "公共单", "清除", "自提"]

# LTL最高优先级识别词：哪怕运输类型显示FTL，只要备注命中这些词，功能一先按LTL处理。
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
    - LTL不参与派送时效统计，时效留空；
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
    thresholds = _delivery_time_threshold_series(out)
    invalid = duration.isna() | (duration <= 0) | duration.gt(thresholds) | _is_ltl_series(out)

    if start_col:
        start_time = pd.to_datetime(out[start_col], errors="coerce")
        invalid = invalid | start_time.isna()
    if end_col:
        end_time = pd.to_datetime(out[end_col], errors="coerce")
        invalid = invalid | end_time.isna()

    out["派送时效"] = duration.mask(invalid)
    if "是否有效时效" in out.columns:
        out["是否有效时效"] = ~invalid
    return out


def _patch_invalid_batch_keywords(delivery_workflow_module):
    """派送一无效批次剔除关键词补充。"""
    keywords = list(getattr(delivery_workflow_module, "INVALID_BATCH_KEYWORDS", []))
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
    """备注命中LTL/散货/散板时，优先按LTL处理。"""
    if detail_df is None or detail_df.empty:
        return detail_df

    out = detail_df.copy()
    remark_cols = [col for col in LTL_REMARK_COLUMNS if col in out.columns]
    if remark_cols:
        mask = out.apply(lambda row: _contains_any_keyword(_row_text(row, remark_cols), LTL_PRIORITY_KEYWORDS), axis=1)
        if mask.any():
            _set_text_for_mask(out, mask, "标准运输类型", "LTL", create=True)
            _set_text_for_mask(out, mask, "运输类型", "LTL")
            _set_text_for_mask(out, mask, "运输方式", "LTL")
            _set_text_for_mask(out, mask, "派送方式", "散板出库")
            _set_text_for_mask(out, mask, "车型标准值", "不适用")
            _set_text_for_mask(out, mask, "装车类型标准值", "散板")
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
        out.at[idx, "派送成本"] = _aggregate_unique_batch_costs(_batch_costs_by_ordered_batch_ids(matched, batch_keys))
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

    return _clean_delivery_time_columns(out)


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
        out.at[idx, "派送成本"] = _aggregate_unique_batch_costs(_batch_costs_by_ordered_batch_ids(matched, batch_keys))

    return _clean_delivery_time_columns(out)


def _original_file_period_label(df, date_col="批次出库时间"):
    if df is None or df.empty or date_col not in df.columns:
        return "原文件全部时间范围"
    valid_dates = pd.to_datetime(df[date_col], errors="coerce").dropna()
    if valid_dates.empty:
        return "原文件全部时间范围"
    return f"{valid_dates.min().strftime('%Y-%m-%d')} ~ {valid_dates.max().strftime('%Y-%m-%d')}"


def _patch_stage2_original_file_period(delivery_workflow_module):
    """派送功能二支持按原文件时间范围：不拆月/周，按批次出库时间最小值到最大值汇总。"""
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
        out["统计周期"] = _original_file_period_label(out, "批次出库时间")
        return out

    add_analysis_period_with_original_file_range._supports_original_file_period = True
    delivery_workflow_module.add_analysis_period = add_analysis_period_with_original_file_range
    return delivery_workflow_module


def _patch_stage2_prepare_time_rules(delivery_workflow_module):
    """派送二在生成报表前重洗时效：缺日期/0时效/LTL/区域超阈值均不参与均值与P80。"""
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


def _is_la_source(row):
    return str(row.get("仓库", "")).strip() in ["LA", "美西仓", "美西二号仓", "CA"]


def _transfer_target_from_row(row):
    """
    识别LA调拨至 NJ / SAV / DAL 盈仓的数据。
    必须先具备调拨语义，再按调入仓库、备注、车次/批次、专线线路识别目标仓，避免把普通LA干线派送误判为调拨。
    """
    if not _is_la_source(row):
        return ""

    text_fields = ["出库类型", "业务场景", "调入仓库", "邮编来源", "匹配备注集合", "车次号", "批次号集合"]
    text = " ".join(str(row.get(c, "")) for c in text_fields if c in row.index)
    upper_text = text.upper()
    line = str(row.get("专线线路", "")).strip()

    has_transfer_semantics = (
        any(keyword in text for keyword in TRANSFER_KEYWORDS)
        or "仓间调拨目标仓地址" in text
        or bool(str(row.get("调入仓库", "")).strip())
    )
    if not has_transfer_semantics:
        return ""

    for target, target_info in TRANSFER_TARGETS.items():
        info = tool_common.TRANSFER_WAREHOUSE_INFO.get(target, {})
        keywords = [target, target_info["name"], target_info["line"]] + list(info.get("keywords", []))
        if line == target_info["line"] or any(str(keyword).upper() in upper_text for keyword in keywords if keyword):
            return target
    return ""


def _transfer_rows(matched, ftl_only=True):
    if matched is None or matched.empty:
        return pd.DataFrame()
    out = matched.copy()
    out["调拨目标仓"] = out.apply(_transfer_target_from_row, axis=1)
    out = out[out["调拨目标仓"].isin(TRANSFER_TARGETS.keys())].copy()
    if ftl_only and "是否FTL发车" in out.columns:
        out = out[out["是否FTL发车"]].copy()
    for col in ["出库体积", "出库卡板数", "派送成本"]:
        if col not in out.columns:
            out[col] = 0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0)
    out["调拨目标仓名称"] = out["调拨目标仓"].map(lambda x: TRANSFER_TARGETS.get(x, {}).get("name", x))
    out["专线线路"] = out["调拨目标仓"].map(lambda x: TRANSFER_TARGETS.get(x, {}).get("line", ""))
    return out


def _filter_positive_cost_rows(df):
    """成本测算只纳入派送成本大于0的车次；0成本车不计车次数、不参与平均价。"""
    if df is None or df.empty:
        return df
    out = df.copy()
    if "派送成本" not in out.columns:
        return out.iloc[0:0].copy()
    out["派送成本"] = pd.to_numeric(out["派送成本"], errors="coerce").fillna(0)
    return out[out["派送成本"] > 0].copy()


def _filter_regular_single_batch_trips_for_cost(matched):
    """普通派送成本只看单批次单车次、且派送成本大于0；调拨成本另行汇总。"""
    if matched is None or matched.empty:
        return matched
    out = matched.copy()
    transfer_targets = out.apply(_transfer_target_from_row, axis=1)
    regular = out[~transfer_targets.isin(TRANSFER_TARGETS.keys())].copy()
    if regular.empty:
        return regular
    batch_counts = regular.apply(lambda row: len(_unique_batch_keys_from_row(row)), axis=1)
    single_batch = regular[batch_counts == 1].copy()
    return _filter_positive_cost_rows(single_batch)


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
    transfer = _filter_positive_cost_rows(_transfer_rows(matched, ftl_only=True))
    columns = [
        "指标名称", "仓库", "统计周期", "对象类型", "平台", "仓点代码", "车型装车分组",
        "车次数", "总出库体积", "总派送成本", "平均整车价", "每方平均价",
        "平均每车出库体积", "P80每车出库体积",
    ]
    if transfer.empty:
        return pd.DataFrame(columns=columns)

    rows = []
    for (warehouse, period, target, target_name), group in transfer.groupby(["仓库", "统计周期", "调拨目标仓", "调拨目标仓名称"], dropna=False):
        trip_count = len(group)
        total_volume = group["出库体积"].sum()
        total_cost = group["派送成本"].sum()
        average_group = processors.average_sample_rows(group)
        rows.append({
            "指标名称": "调拨成本",
            "仓库": warehouse,
            "统计周期": period,
            "对象类型": "仓间调拨",
            "平台": "联宇盈仓",
            "仓点代码": target_name,
            "车型装车分组": "不区分车型",
            "车次数": int(trip_count),
            "总出库体积": total_volume,
            "总派送成本": total_cost,
            "平均整车价": average_group["派送成本"].mean() if not average_group.empty else pd.NA,
            "每方平均价": processors.mean_detail_ratio(average_group, "派送成本", "出库体积"),
            "平均每车出库体积": average_group["出库体积"].mean() if not average_group.empty else pd.NA,
            "P80每车出库体积": processors.safe_p80(average_group["出库体积"]) if not average_group.empty else pd.NA,
        })
    return pd.DataFrame(rows)[columns]


def _build_transfer_report(matched):
    transfer = _transfer_rows(matched, ftl_only=True)
    columns = [
        "指标名称", "发货仓", "调拨目标仓", "专线线路", "统计周期", "车次数",
        "总出库体积", "总出库卡板数", "总派送成本", "平均整车价", "每方平均价",
        "平均每车出库体积", "批次号集合", "车次号集合",
    ]
    if transfer.empty:
        return pd.DataFrame(columns=columns)

    rows = []
    for (warehouse, period, target_name, line), group in transfer.groupby(["仓库", "统计周期", "调拨目标仓名称", "专线线路"], dropna=False):
        trip_count = len(group)
        total_volume = group["出库体积"].sum()
        total_pallets = group["出库卡板数"].sum()
        total_cost = group["派送成本"].sum()
        average_group = processors.average_sample_rows(group)
        rows.append({
            "指标名称": "LA仓间调拨",
            "发货仓": warehouse,
            "调拨目标仓": target_name,
            "专线线路": line,
            "统计周期": period,
            "车次数": int(trip_count),
            "总出库体积": total_volume,
            "总出库卡板数": total_pallets,
            "总派送成本": total_cost,
            "平均整车价": average_group["派送成本"].mean() if not average_group.empty else pd.NA,
            "每方平均价": processors.mean_detail_ratio(average_group, "派送成本", "出库体积"),
            "平均每车出库体积": average_group["出库体积"].mean() if not average_group.empty else pd.NA,
            "批次号集合": _combine_series_text(group.get("批次号集合")),
            "车次号集合": _combine_series_text(group.get("车次号")),
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
        regular_cost = original_func(_filter_regular_single_batch_trips_for_cost(matched))
        transfer_cost = _build_transfer_cost_report(matched)
        frames = [df for df in [regular_cost, transfer_cost] if df is not None and not df.empty]
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

        cost = delivery_match_adapter.build_station_cost_report(matched)
        transfer_report = _build_transfer_report(matched)
        if "目的地邮编待补充" in matched.columns:
            zip_audit = matched[tool_common.normalize_boolean_series(matched["目的地邮编待补充"])].copy()
        else:
            zip_audit = pd.DataFrame()

        return {
            "货量": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(volume, "货量"), "货量"),
            "FBA货量排行": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(delivery_match_adapter.build_fba_rank_sheet(matched), "FBA货量排行"), "FBA货量排行"),
            "FBX平台仓货量": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(delivery_match_adapter.build_fbx_platform_warehouse_sheet(matched), "FBX平台仓货量"), "FBX平台仓货量"),
            "发车量": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(dispatch, "发车量"), "发车量"),
            "派送时效": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(timing, "派送时效"), "派送时效"),
            "调拨数据": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(transfer_report, "调拨数据"), "调拨数据"),
            "成本": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(cost, "成本"), "成本"),
            "派送二_匹配后合并数据": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(matched, "明细"), "明细"),
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
            cleaned_batches = tool_common.apply_dominant_destination_from_detail(cleaned_batches, raw_detail)
            cleaned_batches = _apply_trip_cost_rule(cleaned_batches, raw_detail)
            cleaned_batches = _clean_delivery_time_columns(cleaned_batches)
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

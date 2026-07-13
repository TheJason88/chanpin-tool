import re

import pandas as pd

import tool_common
import delivery_match_adapter
import delivery_stage1_adapter


ORIGINAL_FILE_PERIOD = "按原文件时间范围"


def _sync_common_rules():
    # 统一字段别名和调拨仓规则，避免多个补丁模块各自维护一套。
    delivery_stage1_adapter.VOLUME_CANDIDATES = tool_common.FIELD_ALIASES["出库体积"]
    delivery_stage1_adapter.PALLET_CANDIDATES = tool_common.FIELD_ALIASES["出库卡板数"]
    delivery_stage1_adapter.COST_CANDIDATES = tool_common.FIELD_ALIASES["派送成本"]
    delivery_stage1_adapter.TRANSFER_WAREHOUSE_INFO = tool_common.TRANSFER_WAREHOUSE_INFO
    delivery_match_adapter.TRANSFER_WAREHOUSE_INFO = tool_common.TRANSFER_WAREHOUSE_INFO
    delivery_match_adapter.INTEGER_COLUMNS = tool_common.INTEGER_OUTPUT_COLUMNS
    delivery_match_adapter.DECIMAL_COLUMNS = tool_common.DECIMAL_OUTPUT_COLUMNS


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

    detail = delivery_stage1_adapter.repair_delivery_stage1_numeric_columns(detail_df)
    detail = delivery_stage1_adapter._ensure_numeric(detail, delivery_stage1_adapter.NUMERIC_COLS)
    if "批次号" not in detail.columns:
        return cleaned_batches
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

    return out


def _apply_trip_cost_rule(cleaned_batches, raw_detail):
    """兜底回填派送成本，确保功能一最终输出仍使用同一套成本口径。"""
    if cleaned_batches is None or cleaned_batches.empty or raw_detail is None or raw_detail.empty:
        return cleaned_batches
    if "批次号" not in raw_detail.columns or "派送成本" not in raw_detail.columns:
        return cleaned_batches

    detail = raw_detail.copy()
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

    return out


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
            cleaned_batches = tool_common.apply_dominant_destination_from_detail(cleaned_batches, raw_detail)
            cleaned_batches = _apply_trip_cost_rule(cleaned_batches, raw_detail)
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
    _patch_stage2_original_file_period(delivery_workflow_module)
    # 关键修正：把成本聚合规则挂到功能一强制回填函数本身，避免后置 wrapper 未生效时派送成本仍按明细简单相加。
    delivery_stage1_adapter._force_cleaned_totals_from_detail = _stage1_force_totals_with_unique_cost_rule
    delivery_match_adapter.patch_delivery_workflow(delivery_workflow_module)
    delivery_stage1_adapter.patch_delivery_stage1(delivery_workflow_module)
    _wrap_stage1_no_time_filter_and_dominant_destination(delivery_workflow_module)
    return delivery_workflow_module

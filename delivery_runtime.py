import re

import pandas as pd

import tool_common
import delivery_match_adapter
import delivery_stage1_adapter


COST_BLANK_THRESHOLD = 12000.0


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
    - 多批次里相同成本只保留一次；
    - 不同成本相加；
    - 聚合后单车成本超过 12000 时留空。
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

    if total > COST_BLANK_THRESHOLD:
        return float("nan")
    return total


def _apply_trip_cost_rule(cleaned_batches, raw_detail):
    """只回填派送成本，不动方数、板数、目的地识别等已稳定逻辑。"""
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

        # 同一批次在原始明细中可能出现多行，派送成本按该批次首个有效成本取一次，避免同批次重复行放大。
        batch_costs = []
        for batch_key in batch_keys:
            values = matched.loc[matched["批次号_匹配Key"] == batch_key, "派送成本"].dropna()
            if not values.empty:
                batch_costs.append(float(values.iloc[0]))

        out.at[idx, "派送成本"] = _aggregate_unique_batch_costs(batch_costs)

    return out


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
    delivery_match_adapter.patch_delivery_workflow(delivery_workflow_module)
    delivery_stage1_adapter.patch_delivery_stage1(delivery_workflow_module)
    _wrap_stage1_no_time_filter_and_dominant_destination(delivery_workflow_module)
    return delivery_workflow_module

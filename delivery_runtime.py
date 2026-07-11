import re

import pandas as pd

import tool_common
import delivery_match_adapter
import delivery_stage1_adapter


def _sync_common_rules():
    """把公共规则同步给两个派送补丁模块。只做赋值，不做复杂状态清理。"""
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
    if re.fullmatch(r"\d+\.0", text):
        return text[:-2]
    return text


def _batch_cost_series_for_trip(detail, batch_ids):
    """
    派送功能一车次合并成本口径：
    - 先按批次号取每个批次的派送成本；
    - 同一批次多行时，派送成本只取该批次首个有效值，避免重复行放大；
    - 后续由 _aggregate_trip_delivery_cost 判断相等取其一或不等相加。
    """
    if detail is None or detail.empty or "批次号" not in detail.columns or "派送成本" not in detail.columns:
        return pd.Series(dtype=float)

    normalized_batch_ids = [_normalize_batch_key(x) for x in batch_ids]
    normalized_batch_ids = [x for x in normalized_batch_ids if x]
    if not normalized_batch_ids:
        return pd.Series(dtype=float)

    working = detail.copy()
    working["批次号_匹配Key"] = working["批次号"].apply(_normalize_batch_key)
    working["派送成本"] = pd.to_numeric(working["派送成本"], errors="coerce")
    matched = working[working["批次号_匹配Key"].isin(normalized_batch_ids)].copy()
    if matched.empty:
        return pd.Series(dtype=float)

    per_batch = matched.dropna(subset=["派送成本"]).groupby("批次号_匹配Key", sort=False)["派送成本"].first()
    ordered_values = []
    for batch_id in normalized_batch_ids:
        if batch_id in per_batch.index:
            ordered_values.append(float(per_batch.loc[batch_id]))
    return pd.Series(ordered_values, dtype=float)


def _aggregate_trip_delivery_cost(batch_costs):
    if batch_costs is None or batch_costs.empty:
        return 0.0
    values = pd.to_numeric(batch_costs, errors="coerce").dropna().astype(float)
    if values.empty:
        return 0.0
    if len(values) > 1 and values.round(6).nunique(dropna=True) == 1:
        return float(values.iloc[0])
    return float(values.sum())


def _apply_equal_cost_rule_to_stage1(cleaned_batches, raw_detail):
    """同一车次下多个批次派送成本完全相等时取其一；不完全相等时相加。"""
    if cleaned_batches is None or cleaned_batches.empty or raw_detail is None or raw_detail.empty:
        return cleaned_batches

    detail = delivery_stage1_adapter.repair_delivery_stage1_numeric_columns(raw_detail)
    if "批次号" not in detail.columns or "派送成本" not in detail.columns:
        return cleaned_batches

    out = cleaned_batches.copy()
    if "派送成本" not in out.columns:
        out["派送成本"] = 0.0

    for idx, row in out.iterrows():
        batch_ids = delivery_stage1_adapter._split_batch_ids(row.get("批次号集合", row.get("批次号", "")))
        if not batch_ids:
            continue
        batch_costs = _batch_cost_series_for_trip(detail, batch_ids)
        if batch_costs.empty:
            continue
        out.at[idx, "派送成本"] = _aggregate_trip_delivery_cost(batch_costs)
    return out


def _wrap_stage1_runtime_rules(delivery_workflow_module):
    """
    只包一层功能一规则：
    1. 功能一不按时间筛选；
    2. 混合车次按最大体积目的地识别；
    3. 相同派送成本取其一。
    """
    base_func = delivery_workflow_module.process_stage1_raw_files_to_cleaned_batches
    if getattr(base_func, "_is_unified_stage1_runtime_wrapper", False):
        return delivery_workflow_module

    def unified_stage1_process(file_dfs, warehouse, period_type="不适用", start_date=None, end_date=None):
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
            cleaned_batches = _apply_equal_cost_rule_to_stage1(cleaned_batches, raw_detail)
            if cleaned_batches is not None and not cleaned_batches.empty and "目的地邮编待补充" in cleaned_batches.columns:
                mask = tool_common.normalize_boolean_series(cleaned_batches["目的地邮编待补充"])
                zip_audit_df = cleaned_batches[mask].copy()
            return cleaned_batches, invalid_detail, zip_audit_df, raw_detail
        return result

    unified_stage1_process._is_unified_stage1_runtime_wrapper = True
    delivery_workflow_module.process_stage1_raw_files_to_cleaned_batches = unified_stage1_process
    return delivery_workflow_module


def _split_stage2_combined_report(combined):
    if combined is None or combined.empty:
        empty = pd.DataFrame()
        return empty, empty, empty
    volume = combined[combined["报告部分"].astype(str).str.startswith("1.")].copy()
    volume = volume[~volume["指标名称"].astype(str).isin(["FBA仓点货量排行", "FBX平台仓货量排行"])]
    dispatch = combined[combined["报告部分"].astype(str).str.startswith("2.")].copy()
    timing = combined[combined["报告部分"].astype(str).str.startswith("3.")].copy()
    return volume, dispatch, timing


def _stage2_zip_audit_rows(matched):
    """兼容 Excel 读入的 True/False 字符串，避免 matched["目的地邮编待补充"] 被 pandas 当成列名索引。"""
    if matched is None or matched.empty or "目的地邮编待补充" not in matched.columns:
        return pd.DataFrame()
    mask = tool_common.normalize_boolean_series(matched["目的地邮编待补充"])
    return matched[mask].copy()


def _build_stage2_report_safe(delivery_workflow_module, cleaned_batches, match_df=None, period_type="按周统计"):
    """功能二统一报告生成。只修正布尔筛选和空表保护，不改变业务指标口径。"""
    matched = delivery_workflow_module.prepare_stage2_for_report(cleaned_batches, match_df, period_type)
    combined = delivery_workflow_module.build_sheet1_volume_dispatch_time_report(matched)
    volume, dispatch, timing = _split_stage2_combined_report(combined)
    cost = delivery_match_adapter.build_station_cost_report(matched)
    zip_audit = _stage2_zip_audit_rows(matched)

    return {
        "货量": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(volume, "货量"), "货量"),
        "FBA货量排行": delivery_match_adapter._safe_round(
            delivery_match_adapter._finalize_sheet(delivery_match_adapter.build_fba_rank_sheet(matched), "FBA货量排行"),
            "FBA货量排行",
        ),
        "FBX平台仓货量": delivery_match_adapter._safe_round(
            delivery_match_adapter._finalize_sheet(delivery_match_adapter.build_fbx_platform_warehouse_sheet(matched), "FBX平台仓货量"),
            "FBX平台仓货量",
        ),
        "发车量": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(dispatch, "发车量"), "发车量"),
        "派送时效": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(timing, "派送时效"), "派送时效"),
        "成本": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(cost, "成本"), "成本"),
        "派送二_匹配后合并数据": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(matched, "明细"), "明细"),
        "邮编异常审核": delivery_match_adapter._finalize_zip_audit_sheet(zip_audit),
        "区域识别规则": delivery_workflow_module.REGION_RULES_DF,
        "干线识别规则": delivery_workflow_module.LINEHAUL_RULES,
    }


def _patch_stage2_runtime_rules(delivery_workflow_module):
    delivery_workflow_module.process_stage2_analysis = lambda cleaned_batches, match_df=None, period_type="按周统计": _build_stage2_report_safe(
        delivery_workflow_module,
        cleaned_batches,
        match_df,
        period_type,
    )
    return delivery_workflow_module


def bootstrap(delivery_workflow_module):
    """派送模块统一初始化入口。保持幂等，不做强制reload，不删除运行时属性。"""
    _sync_common_rules()
    delivery_match_adapter.patch_delivery_workflow(delivery_workflow_module)
    _patch_stage2_runtime_rules(delivery_workflow_module)
    delivery_stage1_adapter.patch_delivery_stage1(delivery_workflow_module)
    _wrap_stage1_runtime_rules(delivery_workflow_module)
    return delivery_workflow_module


def startup_smoke_check(delivery_workflow_module):
    """轻量启动自检：不读取用户文件，只检查关键函数和核心成本规则。"""
    checks = []

    required_functions = [
        "process_stage1_raw_files_to_cleaned_batches",
        "read_stage1_cleaned_batches",
        "process_stage2_analysis",
        "prepare_stage2_for_report",
        "build_sheet1_volume_dispatch_time_report",
    ]
    for name in required_functions:
        checks.append({"项目": f"函数存在-{name}", "是否通过": callable(getattr(delivery_workflow_module, name, None))})

    checks.append({"项目": "成本规则-相等取其一", "是否通过": _aggregate_trip_delivery_cost(pd.Series([9500, 9500])) == 9500})
    checks.append({"项目": "成本规则-不等相加", "是否通过": _aggregate_trip_delivery_cost(pd.Series([9500, 300])) == 9800})

    return checks

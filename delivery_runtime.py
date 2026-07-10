import tool_common
import delivery_match_adapter
import delivery_stage1_adapter


def _sync_common_rules():
    # 统一字段别名和调拨仓规则，避免多个补丁模块各自维护一套。
    delivery_stage1_adapter.VOLUME_CANDIDATES = tool_common.FIELD_ALIASES["出库体积"]
    delivery_stage1_adapter.PALLET_CANDIDATES = tool_common.FIELD_ALIASES["出库卡板数"]
    delivery_stage1_adapter.COST_CANDIDATES = tool_common.FIELD_ALIASES["派送成本"]
    delivery_stage1_adapter.TRANSFER_WAREHOUSE_INFO = tool_common.TRANSFER_WAREHOUSE_INFO
    delivery_match_adapter.TRANSFER_WAREHOUSE_INFO = tool_common.TRANSFER_WAREHOUSE_INFO
    delivery_match_adapter.INTEGER_COLUMNS = tool_common.INTEGER_OUTPUT_COLUMNS
    delivery_match_adapter.DECIMAL_COLUMNS = tool_common.DECIMAL_OUTPUT_COLUMNS


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

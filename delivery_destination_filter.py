import pandas as pd

import processors
import delivery_match_adapter


DESTINATION_TYPES = ["全部", "FBA", "FBX"]


def _is_blank(value):
    return processors.is_blank(value)


def _numeric_value(value):
    value = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return 0.0 if pd.isna(value) else float(value)


def _text(value):
    if _is_blank(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in ["nan", "none", "null", "<na>"] else text


def classify_delivery_destination_type(row):
    """
    派送目的地类型口径：
    - 明确识别为 Amazon/FBA 仓的，归为 FBA；
    - 其他非FBA目的地统一归为 FBX，用于业务上的 FBA / FBX 二分筛选。
    """
    product_text = " ".join(
        _text(row.get(col, ""))
        for col in ["主产品类型", "系统产品类型", "FBA/FBX", "实际目的地", "修正后目的地", "目的地"]
        if col in row.index
    ).upper()

    fba_code_text = " ".join(
        _text(row.get(col, ""))
        for col in ["FBA仓点代码", "FBA仓点代码集合", "FBA仓点"]
        if col in row.index
    )

    fba_volume = _numeric_value(row.get("FBA出库体积", 0))
    fbx_volume = _numeric_value(row.get("FBX出库体积", 0))

    if "仓间调拨" in product_text:
        return "FBX"
    if "FBA" in product_text or "AMAZON" in product_text:
        if "FBX" not in product_text:
            return "FBA"
    if fba_code_text:
        return "FBA"
    if fba_volume > 0 and fbx_volume <= 0:
        return "FBA"
    return "FBX"


def filter_delivery_destination_type(df, destination_type="全部"):
    if df is None or df.empty or destination_type in [None, "", "全部"]:
        return df
    if destination_type not in ["FBA", "FBX"]:
        return df
    out = df.copy()
    out["目的地类型"] = out.apply(classify_delivery_destination_type, axis=1)
    return out[out["目的地类型"] == destination_type].copy()


def rebuild_zip_audit_from_cleaned(cleaned_batches):
    if cleaned_batches is None or cleaned_batches.empty:
        return pd.DataFrame()
    if "目的地邮编待补充" not in cleaned_batches.columns:
        return pd.DataFrame()
    mask = cleaned_batches["目的地邮编待补充"].astype(str).str.lower().isin(["true", "1", "是", "yes"])
    return cleaned_batches[mask].copy()


def build_stage2_report_for_destination(delivery_workflow_module, cleaned_batches, match_df=None, period_type="按周统计", destination_type="全部"):
    matched = delivery_workflow_module.prepare_stage2_for_report(cleaned_batches, match_df, period_type)
    matched = filter_delivery_destination_type(matched, destination_type)

    combined = delivery_workflow_module.build_sheet1_volume_dispatch_time_report(matched)
    if combined.empty:
        volume = dispatch = timing = combined.copy()
    else:
        volume = combined[combined["报告部分"].astype(str).str.startswith("1.")].copy()
        volume = volume[~volume["指标名称"].astype(str).isin(["FBA仓点货量排行", "FBX平台仓货量排行"])]
        dispatch = combined[combined["报告部分"].astype(str).str.startswith("2.")].copy()
        timing = combined[combined["报告部分"].astype(str).str.startswith("3.")].copy()

    cost = delivery_match_adapter.build_station_cost_report(matched)
    zip_audit = matched[matched["目的地邮编待补充"]].copy() if "目的地邮编待补充" in matched.columns else pd.DataFrame()

    return {
        "货量": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(volume, "货量"), "货量"),
        "FBA货量排行": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(delivery_match_adapter.build_fba_rank_sheet(matched), "FBA货量排行"), "FBA货量排行"),
        "FBX平台仓货量": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(delivery_match_adapter.build_fbx_platform_warehouse_sheet(matched), "FBX平台仓货量"), "FBX平台仓货量"),
        "发车量": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(dispatch, "发车量"), "发车量"),
        "派送时效": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(timing, "派送时效"), "派送时效"),
        "成本": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(cost, "成本"), "成本"),
        "派送二_匹配后合并数据": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(matched, "明细"), "明细"),
        "邮编异常审核": delivery_match_adapter._finalize_zip_audit_sheet(zip_audit),
        "区域识别规则": delivery_workflow_module.REGION_RULES_DF,
        "干线识别规则": delivery_workflow_module.LINEHAUL_RULES,
    }

import re

import numpy as np
import pandas as pd

import processors


ZIP_UPDATE_COLUMNS = [
    "补充标准邮编", "待填邮编", "补充邮编", "目的地邮编", "邮编", "标准邮编", "标准邮编集合",
]
STATE_UPDATE_COLUMNS = ["补充目的州", "目的州", "州", "省/州", "省州", "State", "STATE"]
BATCH_KEY_COLUMNS = ["批次号集合", "批次号"]
MAIN_SHEET_CANDIDATES = [
    "派送二_匹配后合并数据",
    "清洗后数据",
    "派送一_清洗合并数据",
    "派送二_批次车次聚合",
]
AUDIT_SHEET_NAME = "邮编异常审核"
LINEHAUL_ROUTES = ["LA-NJ", "LA-DAL", "LA-CHI", "LA-SAV"]
LINEHAUL_TARGET_NAMES = {
    "LA-NJ": "NJ干线商圈",
    "LA-DAL": "DAL干线商圈",
    "LA-CHI": "CHI干线商圈",
    "LA-SAV": "SAV干线商圈",
}
LINEHAUL_SHEET_COLUMNS = [
    "指标名称", "发货仓", "干线目标区域", "专线线路", "统计周期", "车次数",
    "总出库体积", "总出库卡板数", "总派送成本", "平均整车价", "每方平均价",
    "平均每车出库体积", "平均派送时效", "P80派送时效", "批次号集合", "车次号集合",
]

# 干线商圈口径：NJ/DAL维持原规则；CHI/SAV按实际物流商圈扩大。
LINEHAUL_MARKET_RULES = pd.DataFrame([
    {"干线区域": "NJ州", "专线线路": "LA-NJ", "邮编规则": "070-089", "地区规则": "NJ"},
    {"干线区域": "Dallas, TX", "专线线路": "LA-DAL", "邮编规则": "750-753", "地区规则": "Dallas / TX"},
    {
        "干线区域": "Chicago商圈",
        "专线线路": "LA-CHI",
        "邮编规则": "600-608",
        "地区规则": "Chicago及Illinois侧核心仓储带（Chicago / Joliet / Channahon / Monee / Matteson / Aurora等）",
    },
    {
        "干线区域": "Savannah港口商圈",
        "专线线路": "LA-SAV",
        "邮编规则": "313-314",
        "地区规则": "Savannah港口及周边仓储带（Savannah / Garden City / Pooler / Port Wentworth / Rincon / Ellabell等）",
    },
])


def _is_blank(value):
    try:
        if value is None or pd.isna(value):
            return True
    except Exception:
        if value is None:
            return True
    text = str(value).strip()
    return text.lower() in ["", "nan", "none", "null", "<na>", "false"] or text in ["/", "//", ";", ";;", "-", "0"]


def _truthy(value):
    if value is True:
        return True
    return str(value).strip().lower() in ["true", "1", "是", "yes", "y"]


def _normalize_batch_key(value):
    if _is_blank(value):
        return ""
    text = str(value).strip()
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    return text


def _split_batch_keys(value):
    if _is_blank(value):
        return []
    text = str(value).replace("\n", ",")
    parts = re.split(r"[,，;；/\s]+", text)
    result = []
    for part in parts:
        key = _normalize_batch_key(part)
        if key and key not in result:
            result.append(key)
    return result


def _combine_unique_text(values):
    result = []
    if values is None:
        return ""
    for value in values:
        if _is_blank(value):
            continue
        for part in re.split(r"[,，;；\n]+", str(value)):
            text = part.strip()
            if text and text not in result:
                result.append(text)
    return ",".join(result)


def _batch_tokens_from_row(row):
    tokens = []
    for col in BATCH_KEY_COLUMNS:
        if col in row.index:
            tokens.extend(_split_batch_keys(row.get(col)))
    return set(tokens)


def _normalize_zip(value):
    if _is_blank(value):
        return ""
    text = str(value).strip()
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    try:
        zip_code, _, valid, _ = processors.normalize_zip_value(text)
        if valid and zip_code:
            return str(zip_code).zfill(5)
    except Exception:
        pass
    match5 = re.search(r"(?<!\d)(\d{5})(?!\d)", text)
    if match5:
        return match5.group(1)
    match4 = re.search(r"(?<!\d)(\d{4})(?!\d)", text)
    if match4:
        return "0" + match4.group(1)
    return ""


def _zip_values_from_cell(value):
    if _is_blank(value):
        return []
    parts = re.split(r"[,，;；/\s]+", str(value))
    result = []
    for part in parts:
        zip_code = _normalize_zip(part)
        if zip_code and zip_code not in result:
            result.append(zip_code)
    return result


def _zip_values_from_row(row):
    result = []
    for col in ZIP_UPDATE_COLUMNS:
        if col not in row.index:
            continue
        for zip_code in _zip_values_from_cell(row.get(col)):
            if zip_code not in result:
                result.append(zip_code)
    return result


def _state_from_row(row):
    for col in STATE_UPDATE_COLUMNS:
        if col in row.index and not _is_blank(row.get(col)):
            text = str(row.get(col)).strip().upper()
            match = re.search(r"\b([A-Z]{2})\b", text)
            return match.group(1) if match else text
    return ""


def _build_batch_token_index(df):
    token_index = {}
    for idx, row in df.iterrows():
        for token in _batch_tokens_from_row(row):
            token_index.setdefault(token, set()).add(idx)
    return token_index


def _build_analysis_id_index(df):
    id_index = {}
    if "分析批次ID" not in df.columns:
        return id_index
    for idx, value in df["分析批次ID"].items():
        if not _is_blank(value):
            id_index.setdefault(str(value).strip(), set()).add(idx)
    return id_index


def _ensure_object_columns(df, columns):
    """Pandas 3 + Arrow string columns reject bool assignment; use object dtype before backfill writes."""
    for col in columns:
        if col not in df.columns:
            df[col] = ""
        else:
            df[col] = df[col].astype("object")
    return df


def _market_line_from_zip(zip_code):
    """功能二干线邮编识别：NJ/DAL不变，CHI/SAV扩大到审慎的核心商圈。"""
    z = _normalize_zip(zip_code)
    if len(z) != 5:
        return "", ""
    prefix3 = int(z[:3])
    if 70 <= prefix3 <= 89:
        return "LA-NJ", "干线规则：NJ州邮编070-089"
    if 750 <= prefix3 <= 753:
        return "LA-DAL", "干线规则：Dallas TX邮编750-753"
    if 600 <= prefix3 <= 608:
        return "LA-CHI", "干线规则：Chicago商圈邮编600-608"
    if 313 <= prefix3 <= 314:
        return "LA-SAV", "干线规则：Savannah港口商圈邮编313-314"
    return "未知线路", "邮编未命中干线规则"


def apply_linehaul_market_rules():
    """按需覆盖功能二干线规则，不进入首页启动链路。"""
    import delivery_workflow

    delivery_workflow.LINEHAUL_RULES = LINEHAUL_MARKET_RULES.copy()
    delivery_workflow.line_from_zip = _market_line_from_zip
    return delivery_workflow


def _build_linehaul_sheet(matched):
    """
    生成LA四条干线的独立结果表，字段丰富度与调拨数据保持一致：
    - 每个统计周期固定输出 LA-NJ / LA-DAL / LA-CHI / LA-SAV 四行；
    - 全部指标按FTL车次汇总，LTL不计入干线车次、成本及货量；
    - 同时输出体积、板数、成本、均价、平均装载、平均/P80时效、批次和车次信息。
    """
    if matched is None or getattr(matched, "empty", True):
        return pd.DataFrame(columns=LINEHAUL_SHEET_COLUMNS)
    required = ["仓库", "统计周期", "专线线路"]
    if any(col not in matched.columns for col in required):
        return pd.DataFrame(columns=LINEHAUL_SHEET_COLUMNS)

    source = matched.copy()
    source = source[source["仓库"].astype(str).str.strip().isin(["LA", "美西仓", "美西二号仓", "CA"])].copy()
    if source.empty:
        return pd.DataFrame(columns=LINEHAUL_SHEET_COLUMNS)

    for col in ["出库体积", "出库卡板数", "派送成本", "派送时效"]:
        if col not in source.columns:
            source[col] = pd.NA
        source[col] = pd.to_numeric(source[col], errors="coerce")

    if "是否FTL发车" in source.columns:
        source["_是否FTL"] = source["是否FTL发车"].apply(_truthy)
    else:
        source["_是否FTL"] = source.get("标准运输类型", pd.Series("", index=source.index)).astype(str).str.upper().eq("FTL")

    if "批次号集合" not in source.columns:
        source["批次号集合"] = source.get("批次号", "")
    if "车次号" not in source.columns:
        source["车次号"] = ""

    periods = [p for p in source["统计周期"].dropna().astype(str).unique().tolist() if p.strip()]
    periods = sorted(periods)

    rows = []
    for period in periods:
        period_source = source[source["统计周期"].astype(str) == period]
        for line in LINEHAUL_ROUTES:
            group = period_source[
                (period_source["专线线路"].astype(str).str.strip() == line)
                & period_source["_是否FTL"]
            ].copy()

            trip_count = int(len(group))
            total_volume = float(group["出库体积"].sum(min_count=1)) if not group.empty and group["出库体积"].notna().any() else 0.0
            total_pallets = float(group["出库卡板数"].sum(min_count=1)) if not group.empty and group["出库卡板数"].notna().any() else 0.0
            total_cost = float(group["派送成本"].sum(min_count=1)) if not group.empty and group["派送成本"].notna().any() else 0.0
            average_group = processors.average_sample_rows(group)
            valid_duration = average_group["派送时效"].dropna() if not average_group.empty else pd.Series(dtype="float64")

            rows.append({
                "指标名称": "LA干线数据",
                "发货仓": "LA",
                "干线目标区域": LINEHAUL_TARGET_NAMES[line],
                "专线线路": line,
                "统计周期": period,
                "车次数": trip_count,
                "总出库体积": total_volume,
                "总出库卡板数": total_pallets,
                "总派送成本": total_cost,
                "平均整车价": average_group["派送成本"].mean() if not average_group.empty else pd.NA,
                "每方平均价": processors.mean_detail_ratio(average_group, "派送成本", "出库体积"),
                "平均每车出库体积": average_group["出库体积"].mean() if not average_group.empty else pd.NA,
                "平均派送时效": valid_duration.mean() if not valid_duration.empty else pd.NA,
                "P80派送时效": processors.safe_p80(valid_duration) if not valid_duration.empty else pd.NA,
                "批次号集合": _combine_unique_text(group["批次号集合"]) if not group.empty else "",
                "车次号集合": _combine_unique_text(group["车次号"]) if not group.empty else "",
            })

    result = pd.DataFrame(rows, columns=LINEHAUL_SHEET_COLUMNS)
    result["车次数"] = pd.to_numeric(result["车次数"], errors="coerce").fillna(0).round(0).astype("Int64")
    result["总出库卡板数"] = pd.to_numeric(result["总出库卡板数"], errors="coerce").fillna(0).round(0).astype("Int64")
    for col in [
        "总出库体积", "总派送成本", "平均整车价", "每方平均价",
        "平均每车出库体积", "平均派送时效", "P80派送时效",
    ]:
        result[col] = pd.to_numeric(result[col], errors="coerce").round(2)
    return result


def _insert_sheet_after(report_dict, after_name, sheet_name, sheet_df):
    """按Excel工作表顺序，把新表插到指定工作表之后。"""
    out = {}
    inserted = False
    for name, data in report_dict.items():
        if name == sheet_name:
            continue
        out[name] = data
        if name == after_name:
            out[sheet_name] = sheet_df
            inserted = True
    if not inserted:
        out[sheet_name] = sheet_df
    return out


def apply_stage2_linehaul_sheet_patch():
    """功能二LA结果在“调拨数据”后增加“干线数据”表；非LA结果保持原样。"""
    import delivery_match_adapter

    current = delivery_match_adapter.build_split_stage2_report
    if getattr(current, "_includes_la_linehaul_sheet_v2", False):
        return delivery_match_adapter

    def build_split_stage2_report_with_linehaul(delivery_workflow_module, cleaned_batches, match_df, period_type="按周统计"):
        reports = current(delivery_workflow_module, cleaned_batches, match_df, period_type)
        if not isinstance(reports, dict):
            return reports

        matched = reports.get("派送二_匹配后合并数据")
        linehaul_sheet = _build_linehaul_sheet(matched)
        if linehaul_sheet.empty:
            return reports

        return _insert_sheet_after(reports, "调拨数据", "干线数据", linehaul_sheet)

    build_split_stage2_report_with_linehaul._includes_la_linehaul_sheet_v2 = True
    delivery_match_adapter.build_split_stage2_report = build_split_stage2_report_with_linehaul
    return delivery_match_adapter


def apply_zip_audit_updates(main_df, audit_df):
    """
    Consume the filled 邮编异常审核 sheet and write the补充邮编 back to the main stage-2 data.
    Matching priority is 批次号集合/批次号, then 分析批次ID as fallback.
    """
    if main_df is None or getattr(main_df, "empty", True) or audit_df is None or getattr(audit_df, "empty", True):
        return main_df

    df = main_df.copy()
    audit = audit_df.copy()
    backfill_columns = ["标准邮编集合", "邮编前三位集合", "目的州", "邮编来源", "目的地邮编待补充"]
    df = _ensure_object_columns(df, backfill_columns)

    token_index = _build_batch_token_index(df)
    id_index = _build_analysis_id_index(df)

    for _, audit_row in audit.iterrows():
        zips = _zip_values_from_row(audit_row)
        if not zips:
            continue

        target_indexes = set()
        audit_tokens = _batch_tokens_from_row(audit_row)
        if audit_tokens:
            for token in audit_tokens:
                target_indexes.update(token_index.get(token, set()))

        if not target_indexes and "分析批次ID" in audit_row.index and not _is_blank(audit_row.get("分析批次ID")):
            target_indexes.update(id_index.get(str(audit_row.get("分析批次ID")).strip(), set()))

        state = _state_from_row(audit_row)
        for idx in target_indexes:
            df.at[idx, "标准邮编集合"] = ",".join(zips)
            df.at[idx, "邮编前三位集合"] = ",".join([z[:3] for z in zips if len(z) == 5])
            if state:
                df.at[idx, "目的州"] = state
            df.at[idx, "邮编来源"] = "邮编异常审核人工补充"
            df.at[idx, "目的地邮编待补充"] = False

    df["目的地邮编待补充"] = df["标准邮编集合"].apply(lambda value: len(_zip_values_from_cell(value)) == 0).astype("object")
    return df


def read_stage1_or_stage2_with_audit_updates(excel_file):
    # 功能二开始读取5A时同步应用最新干线商圈规则和LA干线独立表。
    apply_linehaul_market_rules()
    apply_stage2_linehaul_sheet_patch()

    excel_file.seek(0)
    xls = pd.ExcelFile(excel_file)
    sheet_name = xls.sheet_names[0]
    for candidate in MAIN_SHEET_CANDIDATES:
        if candidate in xls.sheet_names:
            sheet_name = candidate
            break

    excel_file.seek(0)
    df = pd.read_excel(excel_file, sheet_name=sheet_name, dtype=str)

    if AUDIT_SHEET_NAME in xls.sheet_names:
        excel_file.seek(0)
        audit_df = pd.read_excel(excel_file, sheet_name=AUDIT_SHEET_NAME, dtype=str)
        df = apply_zip_audit_updates(df, audit_df)
    return df


def process_pickup_timing_by_pickup_date(df, warehouse, product_type, period_type, start_date=None, end_date=None):
    """
    提柜分析的统计维度改为“提柜时间”：
    - 日期筛选、周/月归属、柜号去重排序都按提柜时间；
    - 提柜时效仍沿用原口径：LA/NJ/SAV 为 Available时间→实际抵仓时间，其他仓为提柜时间→实际抵仓时间。
    - 开始/结束节点都存在但时效刚好等于0天时，按0.5天计入平均和P80；缺失或小于0仍按异常留空。
    """
    df = processors.prepare_base_df(df)
    df = processors.filter_warehouse(df, warehouse)
    df["客户类型"] = df.apply(processors.classify_customer_type_for_time_ops, axis=1)

    processors.require_columns(df, ["柜号", "提柜时间", "实际抵仓时间"], "提柜分析")
    processors.check_product_channel_available(df, "提柜分析")
    df = processors.filter_date_range(df, "提柜时间", start_date, end_date)
    df = processors.filter_valid_container_rows(df, "提柜分析")

    df["提柜时间"] = pd.to_datetime(df["提柜时间"], errors="coerce")
    df["实际抵仓时间"] = pd.to_datetime(df["实际抵仓时间"], errors="coerce")
    if "Available时间" in df.columns:
        df["Available时间"] = pd.to_datetime(df["Available时间"], errors="coerce")
    else:
        df["Available时间"] = pd.NaT

    df = processors.deduplicate_by_container_no(df, sort_col="提柜时间")
    df = processors.add_period_column(df, period_type, "提柜时间")
    df["T渠道类型"] = df["产品渠道"].apply(processors.classify_t_channel)
    df["开始时间"] = np.where(df["仓库"].isin(["LA", "NJ", "SAV"]), df["Available时间"], df["提柜时间"])
    df["开始时间"] = pd.to_datetime(df["开始时间"], errors="coerce")
    df["结束时间"] = df["实际抵仓时间"]
    df["提柜时效"] = (df["结束时间"] - df["开始时间"]).dt.total_seconds() / 86400
    zero_duration_mask = df["开始时间"].notna() & df["结束时间"].notna() & df["提柜时效"].eq(0)
    df.loc[zero_duration_mask, "提柜时效"] = 0.5
    df = processors.mark_duration_abnormal(df, "提柜时效", "开始时间", "结束时间", min_days=0.01, max_days=20)

    detail_df = df.copy()
    detail_df.loc[~detail_df["是否有效"], "提柜时效"] = np.nan
    result_df = processors.build_time_ops_one_row_summary(detail_df, "提柜时效", "提柜")
    result_df = processors.round_output_numbers(result_df, processors.RESULT_DECIMALS)
    return detail_df, result_df


def apply_pickup_action_date_patch():
    current = getattr(processors, "process_pickup_timing", None)
    if current is None or getattr(current, "_uses_pickup_time_for_action_count", False):
        return
    processors._original_process_pickup_timing = current
    process_pickup_timing_by_pickup_date._uses_pickup_time_for_action_count = True
    processors.process_pickup_timing = process_pickup_timing_by_pickup_date


def apply_to_workflow(delivery_workflow_module):
    delivery_workflow_module.read_stage1_cleaned_batches = read_stage1_or_stage2_with_audit_updates
    apply_linehaul_market_rules()
    apply_stage2_linehaul_sheet_patch()
    apply_pickup_action_date_patch()
    return delivery_workflow_module

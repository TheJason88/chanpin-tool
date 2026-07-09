import re

import numpy as np
import pandas as pd

import processors
import delivery_reference


LINEHAUL_RULES = pd.DataFrame([
    {"干线区域": "NJ州", "专线线路": "LA-NJ", "邮编规则": "070-089", "地区规则": "NJ"},
    {"干线区域": "Dallas, TX", "专线线路": "LA-DAL", "邮编规则": "750-753", "地区规则": "Dallas / TX"},
    {"干线区域": "Chicago, IL", "专线线路": "LA-CHI", "邮编规则": "606xx", "地区规则": "Chicago / IL"},
    {"干线区域": "Savannah, GA", "专线线路": "LA-SAV", "邮编规则": "314xx", "地区规则": "Savannah / GA"},
])


FALSE_VALUES = {"false", "0", "否", "no", "n", "nan", "none", ""}
TRUE_VALUES = {"true", "1", "是", "yes", "y"}
INVALID_BATCH_KEYWORDS = ["取消", "作废", "废单", "无效", "删除", "关闭"]


def truthy(value):
    if value is True:
        return True
    return str(value).strip().lower() in TRUE_VALUES


def is_invalid_batch_status(value):
    if processors.is_blank(value):
        return False
    text = str(value)
    return any(keyword in text for keyword in INVALID_BATCH_KEYWORDS)


def split_values(value):
    if processors.is_blank(value):
        return []
    parts = re.split(r"[,，;；/\s]+", str(value))
    return [p.strip() for p in parts if p.strip() and p.strip().lower() not in FALSE_VALUES]


def normalize_zip_for_line(value):
    if processors.is_blank(value):
        return ""
    text = str(value).strip()
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    if re.fullmatch(r"\d{4}", text):
        return "0" + text
    if re.fullmatch(r"\d{5}", text):
        return text
    match = re.search(r"(?<!\d)(\d{5})(?!\d)", text)
    if match:
        return match.group(1)
    match4 = re.search(r"(?<!\d)(\d{4})(?!\d)", text)
    if match4:
        return "0" + match4.group(1)
    return ""


def line_from_zip(zip_code):
    z = normalize_zip_for_line(zip_code)
    if len(z) != 5:
        return "", ""
    prefix3 = int(z[:3])
    if 70 <= prefix3 <= 89:
        return "LA-NJ", "干线规则：NJ州邮编070-089"
    if 750 <= prefix3 <= 753:
        return "LA-DAL", "干线规则：Dallas TX邮编750-753"
    if z.startswith("606"):
        return "LA-CHI", "干线规则：Chicago IL邮编606xx"
    if z.startswith("314"):
        return "LA-SAV", "干线规则：Savannah GA邮编314xx"
    return "未知线路", "邮编未命中干线规则"


def identify_linehaul_second_part(row):
    """干线区域识别只用于第二部分派送数据匹配/最终分析。"""
    if str(row.get("仓库", "")).strip() not in ["LA", "美西仓", "美西二号仓", "CA"]:
        return "非LA干线", "非LA仓暂不识别LA干线"

    outbound_type = str(row.get("出库类型", ""))
    transfer_to = str(row.get("调入仓库", ""))
    if "调拨" in outbound_type:
        for key, line in processors.TRANSFER_WAREHOUSE_TO_LINE.items():
            if key and key in transfer_to:
                return line, "调拨数据：调入仓库映射"

    zip_values = []
    for col in ["标准邮编集合", "标准邮编", "规则匹配邮编", "补充标准邮编"]:
        if col in row.index:
            zip_values.extend(split_values(row.get(col)))
    for z in zip_values:
        line, reason = line_from_zip(z)
        if line != "未知线路":
            return line, reason

    states = [x.upper() for x in split_values(row.get("目的州", ""))]
    dest_text = " ".join(str(row.get(c, "")) for c in ["修正后目的地", "目的地", "平台名称", "规则匹配州"] if c in row.index).upper()
    if "NJ" in states or " NJ" in dest_text:
        return "LA-NJ", "地区规则：NJ"
    if "TX" in states and ("DALLAS" in dest_text or not zip_values):
        return "LA-DAL", "地区规则：TX/Dallas"
    if "IL" in states and ("CHICAGO" in dest_text or not zip_values):
        return "LA-CHI", "地区规则：IL/Chicago"
    if "GA" in states and ("SAVANNAH" in dest_text or not zip_values):
        return "LA-SAV", "地区规则：GA/Savannah"
    return "未知线路", "未命中干线邮编/地区规则"


def remove_invalid_stage1_rows(stage1_detail):
    df = stage1_detail.copy()
    valid_truck = df.get("是否进入卡车派送分析", False)
    if isinstance(valid_truck, pd.Series):
        mask_truck = valid_truck.apply(truthy)
    else:
        mask_truck = pd.Series(False, index=df.index)
    if "批次状态" in df.columns:
        mask_valid_status = ~df["批次状态"].apply(is_invalid_batch_status)
    else:
        mask_valid_status = pd.Series(True, index=df.index)
    df["无效批次剔除原因"] = ""
    df.loc[~mask_truck, "无效批次剔除原因"] = df.loc[~mask_truck, "排除原因"].astype(str) if "排除原因" in df.columns else "非卡车派送"
    df.loc[~mask_valid_status, "无效批次剔除原因"] = "批次状态无效"
    valid_df = df[mask_truck & mask_valid_status].copy()
    invalid_df = df[~(mask_truck & mask_valid_status)].copy()
    return valid_df, invalid_df


def sort_unmatched_zip_bottom(df):
    out = df.copy()
    if "目的地邮编待补充" not in out.columns:
        if "标准邮编集合" in out.columns:
            out["目的地邮编待补充"] = out["标准邮编集合"].apply(lambda x: len(split_values(x)) == 0)
        elif "标准邮编" in out.columns:
            out["目的地邮编待补充"] = out["标准邮编"].apply(lambda x: processors.is_blank(x))
        else:
            out["目的地邮编待补充"] = True
    sort_cols = ["目的地邮编待补充"]
    if "批次出库时间" in out.columns:
        out["批次出库时间"] = pd.to_datetime(out["批次出库时间"], errors="coerce")
        sort_cols.append("批次出库时间")
    elif "出库时间" in out.columns:
        out["出库时间"] = pd.to_datetime(out["出库时间"], errors="coerce")
        sort_cols.append("出库时间")
    return out.sort_values(sort_cols, ascending=[True] * len(sort_cols)).reset_index(drop=True)


def process_stage1_raw_files_to_cleaned_batches(file_dfs, warehouse, period_type="按周统计", start_date=None, end_date=None):
    """
    第一部分：派送原数据处理。
    只做：合并多文件、剔除无效批次、识别FTL/LTL、FTL按车次合并、识别FBA/FBX和邮编；不做干线区域识别、不做最终指标分析。
    """
    detail_df, exclude_df_old, zip_audit_old = processors.process_delivery_stage1_from_files(
        file_dfs=file_dfs,
        warehouse=warehouse,
        period_type=period_type,
        start_date=start_date,
        end_date=end_date,
    )
    detail_df = delivery_reference.apply_delivery_reference_memory(detail_df)
    valid_detail, invalid_detail = remove_invalid_stage1_rows(detail_df)

    # 第一部分输出的主表就是清洗+合并后的数据行：LTL明细保留，FTL按车次号合并。
    cleaned_batches = processors.build_delivery_stage2(valid_detail, period_type)
    if cleaned_batches.empty:
        return cleaned_batches, invalid_detail, cleaned_batches.copy(), detail_df

    # 第一部分不做干线区域识别，避免把分析逻辑提前。
    cleaned_batches = cleaned_batches.drop(columns=["专线线路", "专线识别方式"], errors="ignore")
    cleaned_batches["目的地邮编待补充"] = cleaned_batches["标准邮编集合"].apply(lambda x: len(split_values(x)) == 0) if "标准邮编集合" in cleaned_batches.columns else True
    cleaned_batches = sort_unmatched_zip_bottom(cleaned_batches)
    zip_audit_df = cleaned_batches[cleaned_batches["目的地邮编待补充"]].copy()
    return cleaned_batches, invalid_detail, zip_audit_df, detail_df


def read_stage1_cleaned_batches(excel_file):
    excel_file.seek(0)
    xls = pd.ExcelFile(excel_file)
    preferred = "派送一_清洗合并数据"
    if preferred in xls.sheet_names:
        sheet_name = preferred
    elif "派送二_批次车次聚合" in xls.sheet_names:
        sheet_name = "派送二_批次车次聚合"
    else:
        sheet_name = xls.sheet_names[0]
    excel_file.seek(0)
    return pd.read_excel(excel_file, sheet_name=sheet_name, dtype=str)


def prepare_manual_match(match_df):
    match = processors.normalize_columns(match_df)
    processors.require_columns(match, ["批次号"], "人工目的地匹配文件")
    if "标准邮编" not in match.columns and "目的地邮编" not in match.columns:
        raise ValueError("人工目的地匹配文件需要包含 标准邮编 或 目的地邮编。")
    if "标准邮编" not in match.columns:
        match["标准邮编"] = match["目的地邮编"]
    if "目的州" not in match.columns:
        match["目的州"] = ""
    rows = []
    for _, row in match.iterrows():
        zip_code, fix, valid, reason = processors.normalize_zip_value(row.get("标准邮编"))
        rows.append({
            "批次号": str(row.get("批次号", "")).strip(),
            "补充标准邮编": zip_code,
            "补充目的州": str(row.get("目的州", "")).upper().strip(),
            "补充邮编修正类型": fix,
            "补充邮编是否有效": valid,
            "补充邮编异常原因": reason,
        })
    return pd.DataFrame(rows).drop_duplicates(subset=["批次号"], keep="last")


def apply_manual_match_to_cleaned_batches(cleaned_batches, match_df):
    df = cleaned_batches.copy()
    match = prepare_manual_match(match_df)
    if df.empty or match.empty:
        return df

    match_map = match.set_index("批次号").to_dict("index")
    for idx, row in df.iterrows():
        existing_zips = split_values(row.get("标准邮编集合", ""))
        if existing_zips:
            continue
        batch_ids = split_values(row.get("批次号集合", row.get("批次号", "")))
        candidates = [match_map[b] for b in batch_ids if b in match_map and match_map[b].get("补充邮编是否有效")]
        if not candidates:
            continue
        # 如果一个合并车次里多批次补到多个邮编，保留去重集合。
        zips = list(dict.fromkeys([c["补充标准邮编"] for c in candidates if c.get("补充标准邮编")]))
        states = list(dict.fromkeys([c["补充目的州"] for c in candidates if c.get("补充目的州")]))
        if zips:
            df.at[idx, "标准邮编集合"] = ",".join(zips)
            df.at[idx, "邮编前三位集合"] = ",".join([z[:3] for z in zips if len(z) == 5])
            df.at[idx, "邮编来源"] = "批次号人工匹配补充"
            df.at[idx, "目的地邮编待补充"] = False
        if states:
            df.at[idx, "目的州"] = ",".join(states)
    df["目的地邮编待补充"] = df["标准邮编集合"].apply(lambda x: len(split_values(x)) == 0)
    return sort_unmatched_zip_bottom(df)


def apply_linehaul_rules_second_part(cleaned_batches):
    df = cleaned_batches.copy()
    if df.empty:
        return df
    line_results = df.apply(identify_linehaul_second_part, axis=1, result_type="expand")
    line_results.columns = ["专线线路", "专线识别方式"]
    df = df.drop(columns=["专线线路", "专线识别方式"], errors="ignore")
    df = pd.concat([df, line_results], axis=1)
    return df


def build_stage1_summary(cleaned_batches, invalid_detail, zip_audit_df):
    rows = [
        {"项目": "派送一清洗合并后行数", "数量": len(cleaned_batches)},
        {"项目": "剔除无效批次/非卡车派送明细行数", "数量": len(invalid_detail)},
        {"项目": "清洗合并后待补邮编行数", "数量": len(zip_audit_df)},
    ]
    if "标准运输类型" in cleaned_batches.columns:
        for key, value in cleaned_batches["标准运输类型"].value_counts(dropna=False).items():
            rows.append({"项目": f"运输类型-{key}", "数量": int(value)})
    if "系统产品类型" in cleaned_batches.columns:
        for key, value in cleaned_batches["系统产品类型"].value_counts(dropna=False).items():
            rows.append({"项目": f"系统产品类型-{key}", "数量": int(value)})
    return pd.DataFrame(rows)


def process_stage2_analysis(cleaned_batches, match_df, period_type="按周统计"):
    matched = apply_manual_match_to_cleaned_batches(cleaned_batches, match_df)
    matched = apply_linehaul_rules_second_part(matched)
    metrics = {
        "派送二_匹配后合并数据": matched,
        "FBA_FBX货量比": processors.build_fba_fbx_volume_ratio(matched),
        "发车汇总": processors.build_dispatch_summary(matched),
        "干线发车货量": processors.build_linehaul_summary(matched),
        "派送时效": processors.build_delivery_timing_metrics(matched),
        "平台仓货量": processors.build_platform_volume(matched),
        "邮编异常审核": matched[matched["目的地邮编待补充"]].copy() if "目的地邮编待补充" in matched.columns else pd.DataFrame(),
        "干线识别规则": LINEHAUL_RULES,
        "内置FBA邮编表": delivery_reference.FBA_REFERENCE_DF,
        "内置平台仓邮编表": delivery_reference.PLATFORM_REFERENCE_DF,
    }
    for key, value in metrics.items():
        if isinstance(value, pd.DataFrame):
            metrics[key] = processors.round_output_numbers(value, processors.RESULT_DECIMALS)
    return metrics

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
RATIO_DECIMALS = 2


# =========================
# 基础工具
# =========================

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


def first_nonblank(series):
    for value in series:
        if not processors.is_blank(value):
            return value
    return ""


def combine_unique(series):
    values = [str(v).strip() for v in series if not processors.is_blank(v)]
    values = list(dict.fromkeys(values))
    return ",".join(values)


def safe_num(value):
    return pd.to_numeric(value, errors="coerce").fillna(0)


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


def normalize_ratio_dict(value_dict):
    keys = list(value_dict.keys())
    values = [0 if pd.isna(value_dict[k]) else float(value_dict[k]) for k in keys]
    total = sum(values)
    if total <= 0:
        return {k: 0.0 for k in keys}
    raw = [v / total for v in values]
    rounded = [round(x, RATIO_DECIMALS) for x in raw]
    if rounded:
        rounded[-1] = round(1 - sum(rounded[:-1]), RATIO_DECIMALS)
    return dict(zip(keys, rounded))


def add_ratio_rows(report_rows, report_part, metric_name, warehouse, period, dimension_type, value_dict, unit=""):
    ratios = normalize_ratio_dict(value_dict)
    for dim_value, amount in value_dict.items():
        report_rows.append({
            "报告部分": report_part,
            "指标名称": metric_name,
            "仓库": warehouse,
            "统计周期": period,
            "维度类型": dimension_type,
            "维度值": dim_value,
            "数值": amount,
            "单位": unit,
            "占比": ratios.get(dim_value, 0),
            "出库体积": np.nan,
            "发车数": np.nan,
            "平均派送时效": np.nan,
            "P80派送时效": np.nan,
            "备注": "占比按本指标各维度总数归一化，合计为1.00"
        })


def report_row(report_part, metric_name, warehouse, period, dimension_type, dimension_value, value=np.nan, unit="", share=np.nan, volume=np.nan, dispatch=np.nan, avg_time=np.nan, p80_time=np.nan, note=""):
    return {
        "报告部分": report_part,
        "指标名称": metric_name,
        "仓库": warehouse,
        "统计周期": period,
        "维度类型": dimension_type,
        "维度值": dimension_value,
        "数值": value,
        "单位": unit,
        "占比": share,
        "出库体积": volume,
        "发车数": dispatch,
        "平均派送时效": avg_time,
        "P80派送时效": p80_time,
        "备注": note,
    }


# =========================
# 干线识别：只在第二部分分析阶段启用
# =========================

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
    """干线区域识别只用于第二部分派送数据匹配及分析。干线只对LA仓生效。"""
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
        if line not in ["", "未知线路"]:
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


def apply_linehaul_rules_second_part(cleaned_batches):
    df = cleaned_batches.copy()
    if df.empty:
        return df
    line_results = df.apply(identify_linehaul_second_part, axis=1, result_type="expand")
    line_results.columns = ["专线线路", "专线识别方式"]
    df = df.drop(columns=["专线线路", "专线识别方式"], errors="ignore")
    return pd.concat([df, line_results], axis=1)


# =========================
# 第一部分：合并、剔除、FTL/LTL、FTL按车次号合并、识别FBA/FBX与邮编
# =========================

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
    if "排除原因" in df.columns:
        df.loc[~mask_truck, "无效批次剔除原因"] = df.loc[~mask_truck, "排除原因"].astype(str)
    else:
        df.loc[~mask_truck, "无效批次剔除原因"] = "非卡车派送"
    df.loc[~mask_valid_status, "无效批次剔除原因"] = "批次状态无效"

    valid_df = df[mask_truck & mask_valid_status].copy()
    invalid_df = df[~(mask_truck & mask_valid_status)].copy()
    return valid_df, invalid_df


def resolve_group_loading(series):
    values = [str(v) for v in series if not processors.is_blank(v)]
    if any("地板" in v for v in values):
        return "地板"
    if any("卡板" in v for v in values):
        return "卡板"
    if any("散板" in v for v in values):
        return "散板"
    return "未知装车类型"


def resolve_group_vehicle(series):
    values = [str(v) for v in series if not processors.is_blank(v)]
    if any("53" in v or "大车" in v for v in values):
        return "53尺大车"
    if any("26" in v or "小车" in v for v in values):
        return "26尺小车"
    return "53尺大车"


def build_method(transport_type, vehicle, loading):
    if transport_type == "LTL":
        return "散板出库"
    if transport_type == "FTL":
        if loading in ["卡板", "地板"]:
            return f"{vehicle}-{loading}"
        return f"{vehicle}-未知装车类型"
    return "未知运输类型"


def product_summary_type(fba_volume, fbx_volume, system_types):
    if fba_volume > 0 and fbx_volume > 0:
        return "混合目的地"
    if fba_volume > 0:
        return "FBA"
    if fbx_volume > 0:
        return "FBX"
    values = [v for v in system_types if not processors.is_blank(v)]
    return values[0] if len(set(values)) == 1 else "未知"


def main_product_for_dispatch(row):
    fba = float(row.get("FBA出库体积", 0) or 0)
    fbx = float(row.get("FBX出库体积", 0) or 0)
    if fba <= 0 and fbx <= 0:
        return "未知"
    if fba >= fbx:
        return "FBA"
    return "FBX"


def build_cleaned_batches_from_detail(valid_detail):
    df = valid_detail.copy()
    if df.empty:
        return pd.DataFrame()

    for col in ["出库体积", "出库卡板数", "派送成本"]:
        if col not in df.columns:
            df[col] = 0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    for col in ["出库时间", "签收时间"]:
        if col not in df.columns:
            df[col] = pd.NaT
        df[col] = pd.to_datetime(df[col], errors="coerce")
    for col in ["标准运输类型", "车次号", "批次号", "仓库", "出库类型", "业务场景", "系统产品类型", "FBA/FBX", "平台名称", "标准邮编", "邮编前三位", "目的州", "FBA仓点代码", "装车类型标准值", "车型标准值", "调入仓库", "邮编来源"]:
        if col not in df.columns:
            df[col] = ""

    rows = []
    ftl_df = df[df["标准运输类型"] == "FTL"].copy()
    ltl_df = df[df["标准运输类型"] != "FTL"].copy()

    if not ftl_df.empty:
        ftl_df["车次号"] = ftl_df["车次号"].astype(str).replace({"nan": ""})
        ftl_df["车次聚合键"] = np.where(ftl_df["车次号"].apply(processors.is_blank), "FTL_NO_TRIP_" + ftl_df["原始行号"].astype(str), ftl_df["车次号"])
        for trip, group in ftl_df.groupby("车次聚合键", dropna=False):
            vehicle = resolve_group_vehicle(group["车型标准值"])
            loading = resolve_group_loading(group["装车类型标准值"])
            method = build_method("FTL", vehicle, loading)
            start_time = group["出库时间"].min()
            end_time = group["签收时间"].max()
            duration = (end_time - start_time).total_seconds() / 86400 if pd.notna(start_time) and pd.notna(end_time) else np.nan
            fba_volume = group.loc[group["FBA/FBX"] == "FBA", "出库体积"].sum()
            fbx_volume = group.loc[group["FBA/FBX"] == "FBX", "出库体积"].sum()
            row = {
                "分析批次ID": f"FTL_{trip}",
                "仓库": first_nonblank(group["仓库"]),
                "标准运输类型": "FTL",
                "派送方式": method,
                "车型标准值": vehicle,
                "装车类型标准值": loading,
                "车次号": first_nonblank(group["车次号"]),
                "批次号集合": combine_unique(group["批次号"]),
                "出库类型": first_nonblank(group["出库类型"]),
                "业务场景": first_nonblank(group["业务场景"]),
                "调入仓库": first_nonblank(group["调入仓库"]),
                "批次出库时间": start_time,
                "批次签收时间": end_time,
                "派送时效": duration,
                "出库体积": group["出库体积"].sum(),
                "出库卡板数": group["出库卡板数"].sum(),
                "派送成本": group["派送成本"].sum(),
                "FBA出库体积": fba_volume,
                "FBX出库体积": fbx_volume,
                "系统产品类型": product_summary_type(fba_volume, fbx_volume, group["系统产品类型"].astype(str).tolist()),
                "主产品类型": "FBA" if fba_volume >= fbx_volume and fba_volume > 0 else ("FBX" if fbx_volume > 0 else "未知"),
                "平台名称": combine_unique(group["平台名称"]),
                "FBA仓点代码集合": combine_unique(group["FBA仓点代码"]),
                "标准邮编集合": combine_unique(group["标准邮编"]),
                "邮编前三位集合": combine_unique(group["邮编前三位"]),
                "目的州": combine_unique(group["目的州"]),
                "邮编来源": combine_unique(group["邮编来源"]),
                "是否混合目的地": (fba_volume > 0 and fbx_volume > 0),
                "是否混装": len(set([x for x in group["装车类型标准值"].astype(str) if not processors.is_blank(x)])) > 1,
            }
            rows.append(row)

    if not ltl_df.empty:
        for _, r in ltl_df.iterrows():
            product_group = "FBA" if r.get("FBA/FBX") == "FBA" else ("FBX" if r.get("FBA/FBX") == "FBX" else "未知")
            row = {
                "分析批次ID": f"LTL_{r.get('原始行号', '')}",
                "仓库": r.get("仓库", ""),
                "标准运输类型": "LTL",
                "派送方式": "散板出库",
                "车型标准值": "不适用",
                "装车类型标准值": "散板",
                "车次号": "",
                "批次号集合": r.get("批次号", ""),
                "出库类型": r.get("出库类型", ""),
                "业务场景": r.get("业务场景", ""),
                "调入仓库": r.get("调入仓库", ""),
                "批次出库时间": r.get("出库时间", pd.NaT),
                "批次签收时间": r.get("签收时间", pd.NaT),
                "派送时效": r.get("派送时效", np.nan),
                "出库体积": r.get("出库体积", 0),
                "出库卡板数": r.get("出库卡板数", 0),
                "派送成本": r.get("派送成本", 0),
                "FBA出库体积": r.get("出库体积", 0) if product_group == "FBA" else 0,
                "FBX出库体积": r.get("出库体积", 0) if product_group == "FBX" else 0,
                "系统产品类型": r.get("系统产品类型", ""),
                "主产品类型": product_group,
                "平台名称": r.get("平台名称", ""),
                "FBA仓点代码集合": r.get("FBA仓点代码", ""),
                "标准邮编集合": r.get("标准邮编", ""),
                "邮编前三位集合": r.get("邮编前三位", ""),
                "目的州": r.get("目的州", ""),
                "邮编来源": r.get("邮编来源", ""),
                "是否混合目的地": False,
                "是否混装": False,
            }
            rows.append(row)

    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result["批次出库时间"] = pd.to_datetime(result["批次出库时间"], errors="coerce")
    result["批次签收时间"] = pd.to_datetime(result["批次签收时间"], errors="coerce")
    result["派送时效"] = pd.to_numeric(result["派送时效"], errors="coerce")
    result["是否有效时效"] = result["批次出库时间"].notna() & result["批次签收时间"].notna() & result["派送时效"].notna() & (result["派送时效"] > 0) & (result["派送时效"] <= 30)
    result.loc[~result["是否有效时效"], "派送时效"] = np.nan
    result["目的地邮编待补充"] = result["标准邮编集合"].apply(lambda x: len(split_values(x)) == 0)
    return sort_unmatched_zip_bottom(result)


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


def process_stage1_raw_files_to_cleaned_batches(file_dfs, warehouse, period_type="不适用", start_date=None, end_date=None):
    detail_df, _, _ = processors.process_delivery_stage1_from_files(
        file_dfs=file_dfs,
        warehouse=warehouse,
        period_type="按周统计",  # 技术字段，第一部分不输出周/月分析。
        start_date=start_date,
        end_date=end_date,
    )
    detail_df = delivery_reference.apply_delivery_reference_memory(detail_df)
    valid_detail, invalid_detail = remove_invalid_stage1_rows(detail_df)
    cleaned_batches = build_cleaned_batches_from_detail(valid_detail)
    zip_audit_df = cleaned_batches[cleaned_batches["目的地邮编待补充"]].copy() if not cleaned_batches.empty else pd.DataFrame()
    return cleaned_batches, invalid_detail, zip_audit_df, detail_df


def build_stage1_summary(cleaned_batches, invalid_detail, zip_audit_df):
    rows = [
        {"项目": "派送一清洗合并后行数", "数量": len(cleaned_batches)},
        {"项目": "剔除无效批次/非卡车派送明细行数", "数量": len(invalid_detail)},
        {"项目": "清洗合并后待补邮编行数", "数量": len(zip_audit_df)},
    ]
    if not cleaned_batches.empty and "标准运输类型" in cleaned_batches.columns:
        for key, value in cleaned_batches["标准运输类型"].value_counts(dropna=False).items():
            rows.append({"项目": f"运输类型-{key}", "数量": int(value)})
    if not cleaned_batches.empty and "系统产品类型" in cleaned_batches.columns:
        for key, value in cleaned_batches["系统产品类型"].value_counts(dropna=False).items():
            rows.append({"项目": f"系统产品类型-{key}", "数量": int(value)})
    return pd.DataFrame(rows)


# =========================
# 第二部分：匹配及分析
# =========================

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


def add_analysis_period(df, period_type):
    out = df.copy()
    out["批次出库时间"] = pd.to_datetime(out["批次出库时间"], errors="coerce")
    if period_type == "按月统计":
        out["统计周期"] = out["批次出库时间"].dt.strftime("%Y-%m")
    else:
        week_start = out["批次出库时间"] - pd.to_timedelta(out["批次出库时间"].dt.weekday, unit="D")
        week_end = week_start + pd.Timedelta(days=6)
        out["统计周期"] = week_start.dt.strftime("%Y-%m-%d") + " ~ " + week_end.dt.strftime("%Y-%m-%d")
    out["统计周期"] = out["统计周期"].fillna("未知周期")
    return out


def prepare_stage2_for_report(cleaned_batches, match_df, period_type):
    matched = apply_manual_match_to_cleaned_batches(cleaned_batches, match_df)
    matched = apply_linehaul_rules_second_part(matched)
    matched = add_analysis_period(matched, period_type)
    for col in ["出库体积", "出库卡板数", "派送成本", "FBA出库体积", "FBX出库体积", "派送时效"]:
        if col not in matched.columns:
            matched[col] = 0
        matched[col] = pd.to_numeric(matched[col], errors="coerce").fillna(0)
    matched["是否FTL发车"] = matched["标准运输类型"].eq("FTL")
    matched["主产品类型"] = matched.apply(main_product_for_dispatch, axis=1)
    return matched


def dispatch_rows(df):
    return df[df["是否FTL发车"]].copy()


def volume_structure_label(row):
    if row.get("标准运输类型") == "LTL":
        return "LTL"
    loading = str(row.get("装车类型标准值", ""))
    if "地板" in loading:
        return "地板"
    if "卡板" in loading:
        return "卡板"
    return "FTL未知装车"


def linehaul_df(df):
    if df.empty or "专线线路" not in df.columns:
        return df.iloc[0:0].copy()
    return df[(df["仓库"] == "LA") & (~df["专线线路"].isin(["", "未知线路", "非LA干线"]))].copy()


def rank_top_bottom(df, group_col, value_col, top_n=10, bottom_n=10):
    agg = df.groupby(group_col, dropna=False)[value_col].sum().reset_index()
    agg = agg[~agg[group_col].apply(processors.is_blank)]
    agg = agg[agg[value_col] > 0].sort_values(value_col, ascending=False)
    if agg.empty:
        return agg
    top = agg.head(top_n).copy()
    top["排行类型"] = f"前{top_n}"
    bottom = agg.tail(bottom_n).copy().sort_values(value_col, ascending=True)
    bottom["排行类型"] = f"后{bottom_n}"
    return pd.concat([top, bottom], ignore_index=True)


def build_sheet1_volume_dispatch_time_report(df):
    rows = []
    if df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["货量结构"] = df.apply(volume_structure_label, axis=1)
    ftl = dispatch_rows(df)

    for (warehouse, period), group in df.groupby(["仓库", "统计周期"], dropna=False):
        # 1. 货量：非LTL vs LTL
        ltl_volume = group.loc[group["标准运输类型"] == "LTL", "出库体积"].sum()
        non_ltl_volume = group.loc[group["标准运输类型"] != "LTL", "出库体积"].sum()
        add_ratio_rows(rows, "1.货量", "非LTL方数比LTL方数", warehouse, period, "运输类型", {"非LTL": non_ltl_volume, "LTL": ltl_volume}, "CBM")

        # 1. 货量：卡板/地板/LTL方数比
        structure = group.groupby("货量结构")["出库体积"].sum().to_dict()
        ordered = {k: structure.get(k, 0) for k in ["卡板", "地板", "LTL"]}
        for k, v in structure.items():
            if k not in ordered:
                ordered[k] = v
        add_ratio_rows(rows, "1.货量", "卡板比地板比LTL方数", warehouse, period, "货量结构", ordered, "CBM")

        # 1. 货量：FBA/FBX
        fba_volume = group["FBA出库体积"].sum()
        fbx_volume = group["FBX出库体积"].sum()
        add_ratio_rows(rows, "1.货量", "FBA比FBX方数", warehouse, period, "产品类型", {"FBA": fba_volume, "FBX": fbx_volume}, "CBM")

        # 1. 货量：FBA仓点排行
        fba_rank_source = group[group["FBA出库体积"] > 0].copy()
        if not fba_rank_source.empty:
            fba_rank = rank_top_bottom(fba_rank_source, "FBA仓点代码集合", "FBA出库体积")
            for _, r in fba_rank.iterrows():
                rows.append(report_row("1.货量", "FBA仓点货量排行", warehouse, period, r.get("排行类型", "排行"), r["FBA仓点代码集合"], value=r["FBA出库体积"], unit="CBM", volume=r["FBA出库体积"]))

        # 1. 货量：FBX平台仓排行
        platform_source = group[(group["FBX出库体积"] > 0) & (~group["平台名称"].apply(processors.is_blank)) & (group["平台名称"] != "非平台/未知")].copy()
        if not platform_source.empty:
            platform_rank = platform_source.groupby("平台名称", dropna=False)["FBX出库体积"].sum().reset_index()
            platform_rank = platform_rank[platform_rank["FBX出库体积"] > 0].sort_values("FBX出库体积", ascending=False)
            for _, r in platform_rank.iterrows():
                rows.append(report_row("1.货量", "FBX平台仓货量排行", warehouse, period, "平台", r["平台名称"], value=r["FBX出库体积"], unit="CBM", volume=r["FBX出库体积"]))

        # 1. 货量：LA干线货量
        if warehouse == "LA":
            lh = linehaul_df(group)
            if not lh.empty:
                for line, lg in lh.groupby("专线线路", dropna=False):
                    rows.append(report_row("1.货量", "LA干线货量", warehouse, period, "干线线路", line, value=lg["出库体积"].sum(), unit="CBM", volume=lg["出库体积"].sum()))

        # 2. 发车：总发车数，LTL不算发车
        ftl_group = group[group["是否FTL发车"]]
        rows.append(report_row("2.发车量", "总发车数", warehouse, period, "发车口径", "FTL车次", value=len(ftl_group), unit="车", dispatch=len(ftl_group), note="LTL不计入发车数"))

        # 2. 发车：地板/卡板
        loading_counts = {
            "地板": int((ftl_group["装车类型标准值"] == "地板").sum()),
            "卡板": int((ftl_group["装车类型标准值"] == "卡板").sum()),
        }
        add_ratio_rows(rows, "2.发车量", "地板发车比卡板发车", warehouse, period, "装车类型", loading_counts, "车")

        # 2. 发车：FBA/FBX
        product_counts = {
            "FBA": int((ftl_group["主产品类型"] == "FBA").sum()),
            "FBX": int((ftl_group["主产品类型"] == "FBX").sum()),
        }
        add_ratio_rows(rows, "2.发车量", "FBA比FBX发车", warehouse, period, "产品类型", product_counts, "车")

        # 2. 发车：区域发车，区域规则暂留待补
        rows.append(report_row("2.发车量", "区域发车数", warehouse, period, "派送区域", "区域规则待补充", value=np.nan, unit="车", note="四仓区域划分逻辑待补充后启用"))

        # 2. 发车：LA干线发车
        if warehouse == "LA":
            lh_ftl = linehaul_df(ftl_group)
            if not lh_ftl.empty:
                for line, lg in lh_ftl.groupby("专线线路", dropna=False):
                    rows.append(report_row("2.发车量", "LA干线发车数", warehouse, period, "干线线路", line, value=len(lg), unit="车", dispatch=len(lg)))

        # 3. 派送时效：分区域，规则待补
        rows.append(report_row("3.派送时效", "分区域派送时效", warehouse, period, "派送区域", "区域规则待补充", avg_time=np.nan, p80_time=np.nan, note="四仓区域划分逻辑待补充后启用"))

        # 3. 派送时效：LA干线时效
        if warehouse == "LA":
            lh_time = linehaul_df(group)
            if not lh_time.empty:
                for line, lg in lh_time.groupby("专线线路", dropna=False):
                    rows.append(report_row("3.派送时效", "LA干线派送时效", warehouse, period, "干线线路", line, avg_time=lg["派送时效"].mean(), p80_time=processors.safe_p80(lg["派送时效"]), note="平均值与P80，仅有效时效参与计算"))

    return pd.DataFrame(rows)


def cost_dimension_label(row):
    if row.get("主产品类型") == "FBA":
        code = str(row.get("FBA仓点代码集合", "")).strip()
        return "FBA", code if code else "FBA未知仓点"
    platform = str(row.get("平台名称", "")).strip()
    if row.get("主产品类型") == "FBX" and platform and platform != "非平台/未知":
        return "FBX平台仓", platform
    return "其他", "其他/非平台"


def cost_vehicle_group(row):
    vehicle = str(row.get("车型标准值", ""))
    loading = str(row.get("装车类型标准值", ""))
    if "26" in vehicle or "小车" in vehicle:
        return "小车"
    if "53" in vehicle or "大车" in vehicle:
        if "卡板" in loading:
            return "大车卡板"
        if "地板" in loading:
            return "大车地板"
        return "大车未知装车"
    return "未知车型"


def build_sheet2_cost_report(df):
    rows = []
    if df.empty:
        return pd.DataFrame()
    ftl = dispatch_rows(df).copy()
    if ftl.empty:
        return pd.DataFrame(columns=["报告部分", "指标名称", "仓库", "统计周期", "对象类型", "对象名称", "车型装车分组", "车次数", "总出库体积", "总派送成本", "平均整车价", "每方平均价", "平均每车出库体积", "P80每车出库体积", "备注"])

    ftl["车型装车分组"] = ftl.apply(cost_vehicle_group, axis=1)
    ftl[["对象类型", "对象名称"]] = ftl.apply(lambda r: pd.Series(cost_dimension_label(r)), axis=1)

    # 满载情况：FTL + 大车 + 地板
    full_load = ftl[(ftl["车型标准值"] == "53尺大车") & (ftl["装车类型标准值"] == "地板")].copy()
    if not full_load.empty:
        for (warehouse, period), group in full_load.groupby(["仓库", "统计周期"], dropna=False):
            rows.append({
                "报告部分": "4.成本",
                "指标名称": "满载情况",
                "仓库": warehouse,
                "统计周期": period,
                "对象类型": "FTL大车地板",
                "对象名称": "全部",
                "车型装车分组": "大车地板",
                "车次数": len(group),
                "总出库体积": group["出库体积"].sum(),
                "总派送成本": group["派送成本"].sum(),
                "平均整车价": np.nan,
                "每方平均价": np.nan,
                "平均每车出库体积": group["出库体积"].mean(),
                "P80每车出库体积": processors.safe_p80(group["出库体积"]),
                "备注": "满载口径：FTL + 53尺大车 + 地板；统计平均每车方数和P80每车方数"
            })

    # FBA + FBX平台仓成本
    cost_source = ftl[ftl["对象类型"].isin(["FBA", "FBX平台仓"])].copy()
    cost_source = cost_source[cost_source["车型装车分组"].isin(["小车", "大车卡板", "大车地板"])]
    if not cost_source.empty:
        for (warehouse, period, obj_type, obj_name, vehicle_group), group in cost_source.groupby(["仓库", "统计周期", "对象类型", "对象名称", "车型装车分组"], dropna=False):
            total_cost = group["派送成本"].sum()
            total_volume = group["出库体积"].sum()
            rows.append({
                "报告部分": "4.成本",
                "指标名称": "FBA及FBX平台仓成本",
                "仓库": warehouse,
                "统计周期": period,
                "对象类型": obj_type,
                "对象名称": obj_name,
                "车型装车分组": vehicle_group,
                "车次数": len(group),
                "总出库体积": total_volume,
                "总派送成本": total_cost,
                "平均整车价": group["派送成本"].mean(),
                "每方平均价": processors.safe_divide(total_cost, total_volume),
                "平均每车出库体积": group["出库体积"].mean(),
                "P80每车出库体积": processors.safe_p80(group["出库体积"]),
                "备注": "小车不区分卡板/地板；大车区分卡板与地板"
            })

    return pd.DataFrame(rows)


def process_stage2_analysis(cleaned_batches, match_df, period_type="按周统计"):
    matched = prepare_stage2_for_report(cleaned_batches, match_df, period_type)
    sheet1 = build_sheet1_volume_dispatch_time_report(matched)
    sheet2 = build_sheet2_cost_report(matched)
    metrics = {
        "表一_货量发车时效": processors.round_output_numbers(sheet1, processors.RESULT_DECIMALS),
        "表二_成本": processors.round_output_numbers(sheet2, processors.RESULT_DECIMALS),
        "派送二_匹配后合并数据": processors.round_output_numbers(matched, processors.RESULT_DECIMALS),
        "邮编异常审核": matched[matched["目的地邮编待补充"]].copy() if "目的地邮编待补充" in matched.columns else pd.DataFrame(),
        "干线识别规则": LINEHAUL_RULES,
        "内置FBA邮编表": delivery_reference.FBA_REFERENCE_DF,
        "内置平台仓邮编表": delivery_reference.PLATFORM_REFERENCE_DF,
    }
    return metrics

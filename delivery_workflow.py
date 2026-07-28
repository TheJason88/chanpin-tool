import json
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

REGION_ZIP_RULES = {
    "LA": [
        {"区域": "Local", "来源州/区域": "S.CA", "ranges": [(900, 935)]},
        {"区域": "中短途", "来源州/区域": "OR,AZ,N.CA,NV,UT", "ranges": [(970, 979), (850, 865), (936, 961), (889, 898), (840, 847)]},
        {"区域": "LA-美中", "来源州/区域": "IL,IN,TX,KS,ID,IA,CO,KY,OK,NM,TN,MO,WA,WI,MS", "ranges": [(600, 629), (460, 479), (733, 733), (750, 799), (885, 885), (660, 679), (832, 838), (500, 528), (800, 816), (400, 427), (730, 749), (870, 884), (370, 385), (630, 658), (980, 994), (530, 549), (386, 397)]},
        {"区域": "LA-美东", "来源州/区域": "OH,NJ,PA,VA,NY,MA,CT,RI,ME,NH,VT,MD,DE", "ranges": [(430, 459), (70, 89), (150, 196), (201, 201), (220, 246), (5, 5), (100, 149), (10, 27), (55, 55), (60, 69), (28, 29), (39, 49), (30, 38), (50, 59), (206, 219), (197, 199)]},
        {"区域": "LA-美南", "来源州/区域": "GA,MI,NC,SC,FL", "ranges": [(300, 319), (398, 399), (480, 499), (270, 289), (290, 299), (320, 349)]},
    ],
    "NJ": [
        {"区域": "Local", "来源州/区域": "NJ", "ranges": [(70, 89)]},
        {"区域": "中距离", "来源州/区域": "NY,PA,OH,DE,MD,VA,CT,MA,RI,NH,VT,ME,NC,SC,TN,KY,IN,MI", "ranges": [(5, 5), (100, 149), (150, 196), (430, 459), (197, 199), (206, 219), (201, 201), (220, 246), (60, 69), (10, 27), (55, 55), (28, 29), (30, 38), (50, 59), (39, 49), (270, 289), (290, 299), (370, 385), (400, 427), (460, 479), (480, 499)]},
        {"区域": "远距离", "来源州/区域": "AL,AK,AR,AZ,CA,CO,FL,GA,HI,IA,ID,IL,KS,LA,MN,MO,MS,MT,ND,NE,NM,NV,OK,OR,SD,TX,UT,WA,WI,WV,WY,DC", "ranges": [(350, 369), (995, 999), (716, 729), (755, 755), (850, 865), (900, 961), (800, 816), (320, 349), (300, 319), (398, 399), (967, 968), (500, 528), (832, 838), (600, 629), (660, 679), (700, 714), (550, 567), (630, 658), (386, 397), (590, 599), (580, 588), (680, 693), (870, 884), (889, 898), (730, 749), (970, 979), (570, 577), (733, 733), (750, 799), (885, 885), (840, 847), (980, 994), (530, 549), (247, 268), (820, 831), (200, 205), (569, 569)]},
    ],
    "SAV": [
        {"区域": "Local", "来源州/区域": "GA,SC", "ranges": [(300, 319), (398, 399), (290, 299)]},
        {"区域": "中距离", "来源州/区域": "FL,NC,AL,TN,KY,VA,MS,LA,AR,WV,OH", "ranges": [(320, 349), (270, 289), (350, 369), (370, 385), (400, 427), (201, 201), (220, 246), (386, 397), (700, 714), (716, 729), (755, 755), (247, 268), (430, 459)]},
        {"区域": "远距离", "来源州/区域": "AK,AZ,CA,CO,CT,DC,DE,HI,IA,ID,IL,IN,KS,MA,MD,ME,MI,MN,MO,MT,ND,NE,NH,NJ,NM,NV,NY,OK,OR,PA,RI,SD,TX,UT,VT,WA,WI,WY", "ranges": [(995, 999), (850, 865), (900, 961), (800, 816), (60, 69), (200, 205), (569, 569), (197, 199), (967, 968), (500, 528), (832, 838), (600, 629), (460, 479), (660, 679), (10, 27), (55, 55), (206, 219), (39, 49), (480, 499), (550, 567), (630, 658), (590, 599), (580, 588), (680, 693), (30, 38), (70, 89), (870, 884), (889, 898), (5, 5), (100, 149), (730, 749), (970, 979), (150, 196), (28, 29), (570, 577), (733, 733), (750, 799), (885, 885), (840, 847), (50, 59), (980, 994), (530, 549), (820, 831)]},
    ],
    "DAL": [
        {"区域": "Local", "来源州/区域": "TX,OK,AR", "ranges": [(733, 733), (750, 799), (885, 885), (730, 749), (716, 729), (755, 755)]},
        {"区域": "中距离", "来源州/区域": "LA,MS,NM,CO,KS,MO,AL,TN", "ranges": [(700, 714), (386, 397), (870, 884), (800, 816), (660, 679), (630, 658), (350, 369), (370, 385)]},
        {"区域": "远距离", "来源州/区域": "AK,AZ,CA,CT,DC,DE,FL,GA,HI,IA,ID,IL,IN,KY,MA,MD,ME,MI,MN,MT,NC,ND,NE,NH,NJ,NV,NY,OH,OR,PA,RI,SC,SD,UT,VA,VT,WA,WI,WV,WY", "ranges": [(995, 999), (850, 865), (900, 961), (60, 69), (200, 205), (569, 569), (197, 199), (320, 349), (300, 319), (398, 399), (967, 968), (500, 528), (832, 838), (600, 629), (460, 479), (400, 427), (10, 27), (55, 55), (206, 219), (39, 49), (480, 499), (550, 567), (590, 599), (270, 289), (580, 588), (680, 693), (30, 38), (70, 89), (889, 898), (5, 5), (100, 149), (430, 459), (970, 979), (150, 196), (28, 29), (290, 299), (570, 577), (840, 847), (201, 201), (220, 246), (50, 59), (980, 994), (530, 549), (247, 268), (820, 831)]},
    ],
}

REGION_RULES_DF = pd.DataFrame([
    {"发货仓": wh, "区域": rule["区域"], "来源州/区域": rule["来源州/区域"], "邮编前三位规则": "; ".join([f"{a:03d}-{b:03d}" if a != b else f"{a:03d}" for a, b in rule["ranges"]])}
    for wh, rules in REGION_ZIP_RULES.items()
    for rule in rules
])

FALSE_VALUES = {"false", "0", "否", "no", "n", "nan", "none", ""}
TRUE_VALUES = {"true", "1", "是", "yes", "y"}
INVALID_BATCH_KEYWORDS = ["取消", "作废", "废单", "无效", "删除", "关闭"]
RATIO_DECIMALS = 2


def truthy(value):
    if value is True:
        return True
    return str(value).strip().lower() in TRUE_VALUES


def text_contains_invalid_keyword(value):
    if processors.is_blank(value):
        return False
    return any(keyword in str(value) for keyword in INVALID_BATCH_KEYWORDS)


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
    return ",".join(list(dict.fromkeys(values)))


def combine_platform_code_pairs(group):
    pairs = []
    for _, row in group.iterrows():
        platform = str(row.get("平台名称", "")).strip()
        code = str(row.get("FBX代码", "")).strip()
        if processors.is_blank(platform) or platform == "非平台/未知" or processors.is_blank(code):
            continue
        pair = f"{platform}||{code}"
        if pair not in pairs:
            pairs.append(pair)
    return ";".join(pairs)


def build_destination_allocation_details(group):
    """保留合并车次内每个批次的目的仓、货量和原始派送成本。

    功能二只用批次体积占整车体积的比例计算车次数；批次派送成本必须原样保留，
    不能再从整车汇总成本按体积二次分摊。
    """
    allocations = []
    for _, row in group.iterrows():
        volume = pd.to_numeric(row.get("出库体积", 0), errors="coerce")
        pallets = pd.to_numeric(row.get("出库卡板数", 0), errors="coerce")
        cost = pd.to_numeric(row.get("派送成本", 0), errors="coerce")
        volume = 0.0 if pd.isna(volume) else float(volume)
        pallets = 0.0 if pd.isna(pallets) else float(pallets)
        cost = 0.0 if pd.isna(cost) else float(cost)
        batch_no = str(row.get("批次号", "")).strip()
        product_type = str(row.get("FBA/FBX", "")).strip().upper()

        objects = []
        if product_type == "FBA":
            codes = split_values(row.get("FBA仓点代码", ""))
            objects = [("FBA", "FBA", code) for code in codes]
        elif product_type == "FBX":
            codes = split_values(row.get("FBX代码", ""))
            platforms = split_values(row.get("平台名称", ""))
            platforms = [p for p in platforms if p != "非平台/未知"]
            if len(platforms) == len(codes):
                objects = [("FBX平台仓", platform, code) for platform, code in zip(platforms, codes)]
            elif codes:
                platform = platforms[0] if len(platforms) == 1 else ""
                objects = [("FBX平台仓", platform, code) for code in codes]

        if not objects:
            continue
        object_count = len(objects)
        for object_type, platform, code in objects:
            allocations.append({
                "对象类型": object_type,
                "平台": platform,
                "仓点代码": code,
                "批次号": batch_no,
                "出库体积": volume / object_count,
                "出库卡板数": pallets / object_count,
                "派送成本": cost / object_count,
            })

    return json.dumps(allocations, ensure_ascii=False, separators=(",", ":")) if allocations else ""


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


def zip3_from_value(value):
    z = normalize_zip_for_line(value)
    if len(z) == 5:
        return int(z[:3])
    return None


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
            "报告部分": report_part, "指标名称": metric_name, "仓库": warehouse, "统计周期": period,
            "维度类型": dimension_type, "维度值": dim_value, "数值": amount, "单位": unit,
            "占比": ratios.get(dim_value, 0), "出库体积": np.nan, "发车数": np.nan,
            "平均派送时效": np.nan, "P80派送时效": np.nan,
            "备注": "占比按本指标各维度总数归一化，合计为1.00"
        })


def report_row(report_part, metric_name, warehouse, period, dimension_type, dimension_value, value=np.nan, unit="", share=np.nan, volume=np.nan, dispatch=np.nan, avg_time=np.nan, p80_time=np.nan, note=""):
    return {
        "报告部分": report_part, "指标名称": metric_name, "仓库": warehouse, "统计周期": period,
        "维度类型": dimension_type, "维度值": dimension_value, "数值": value, "单位": unit, "占比": share,
        "出库体积": volume, "发车数": dispatch, "平均派送时效": avg_time, "P80派送时效": p80_time, "备注": note,
    }


def match_region_by_zip(warehouse, zip_code):
    wh = processors.standardize_warehouse(warehouse)
    z3 = zip3_from_value(zip_code)
    if wh not in REGION_ZIP_RULES or z3 is None:
        return "未知区域", "无有效邮编"
    for rule in REGION_ZIP_RULES[wh]:
        if any(start <= z3 <= end for start, end in rule["ranges"]):
            return rule["区域"], f"邮编前三位{z3:03d}命中{rule['来源州/区域']}"
    return "未知区域", "未命中区域邮编规则"


def assign_delivery_region(row):
    zip_values = []
    for col in ["标准邮编集合", "标准邮编", "规则匹配邮编", "补充标准邮编"]:
        if col in row.index:
            zip_values.extend(split_values(row.get(col)))
    regions = []
    reasons = []
    for z in zip_values:
        region, reason = match_region_by_zip(row.get("仓库"), z)
        if region != "未知区域":
            regions.append(region)
            reasons.append(reason)
    unique_regions = list(dict.fromkeys(regions))
    if len(unique_regions) == 1:
        return unique_regions[0], reasons[0]
    if len(unique_regions) > 1:
        return "多区域", "; ".join(reasons)
    return "未知区域", "无邮编或未命中区域规则"


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
    return "未知线路", "未命中干线邮编规则"


def apply_linehaul_rules_second_part(df):
    if df.empty:
        return df
    line_results = df.apply(identify_linehaul_second_part, axis=1, result_type="expand")
    line_results.columns = ["专线线路", "专线识别方式"]
    out = df.drop(columns=["专线线路", "专线识别方式"], errors="ignore")
    return pd.concat([out, line_results], axis=1)


def apply_region_rules_second_part(df):
    if df.empty:
        return df
    region_results = df.apply(assign_delivery_region, axis=1, result_type="expand")
    region_results.columns = ["派送区域", "派送区域识别方式"]
    out = df.drop(columns=["派送区域", "派送区域识别方式"], errors="ignore")
    return pd.concat([out, region_results], axis=1)


def remove_invalid_stage1_rows(stage1_detail):
    df = stage1_detail.copy()
    mask_truck = df.get("是否进入卡车派送分析", pd.Series(False, index=df.index)).apply(truthy)
    mask_valid_status = ~df["批次状态"].apply(text_contains_invalid_keyword) if "批次状态" in df.columns else pd.Series(True, index=df.index)
    mask_valid_remark = ~df["备注"].apply(text_contains_invalid_keyword) if "备注" in df.columns else pd.Series(True, index=df.index)

    df["无效批次剔除原因"] = ""
    if "排除原因" in df.columns:
        df.loc[~mask_truck, "无效批次剔除原因"] = df.loc[~mask_truck, "排除原因"].astype(str)
    else:
        df.loc[~mask_truck, "无效批次剔除原因"] = "非卡车派送"
    df.loc[~mask_valid_status, "无效批次剔除原因"] = df.loc[~mask_valid_status, "无效批次剔除原因"].replace("", "批次状态含无效关键词")
    df.loc[~mask_valid_remark, "无效批次剔除原因"] = df.loc[~mask_valid_remark, "无效批次剔除原因"].apply(lambda x: (x + "; 备注含无效关键词") if str(x).strip() else "备注含无效关键词")

    valid_mask = mask_truck & mask_valid_status & mask_valid_remark
    return df[valid_mask].copy(), df[~valid_mask].copy()


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
        return f"{vehicle}-{loading}" if loading in ["卡板", "地板"] else f"{vehicle}-未知装车类型"
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
    return "FBA" if fba >= fbx else "FBX"


def _clean_text(value):
    if processors.is_blank(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null", "<na>"} else text


def _first_numeric(series, default=0.0):
    values = pd.to_numeric(series, errors="coerce").dropna()
    return float(values.iloc[0]) if not values.empty else float(default)


def _batch_cost_value(series):
    """一个批次只保留一个原始派送成本，并标识同批次成本冲突。"""
    values = pd.to_numeric(series, errors="coerce").dropna().astype(float)
    if values.empty:
        return 0.0, ""
    unique_values = list(dict.fromkeys(round(float(value), 6) for value in values))
    note = "" if len(unique_values) <= 1 else f"同批次存在多个派送成本：{','.join(f'{x:g}' for x in unique_values)}；采用首个有效值"
    return float(values.iloc[0]), note


def _batch_delivery_truck(series):
    """返回批次唯一派送卡车；冲突供应商不做归属，但保留批次货量。"""
    displays = []
    normalized = {}
    for value in series:
        display = re.sub(r"\s+", " ", _clean_text(value)).strip()
        if not display:
            continue
        key = display.casefold()
        if key not in normalized:
            normalized[key] = display
            displays.append(display)
    if not displays:
        return "", ""
    if len(displays) == 1:
        return displays[0], ""
    return "", f"同批次存在多个派送卡车：{','.join(displays)}；货量计入占比分母但不归属供应商"


def _batch_destination(group):
    """返回批次唯一目的地；一个批次出现多个目的地时不做等分，直接转无效审核。"""
    transfer_target = first_nonblank(group.get("调入仓库", pd.Series(dtype=object)))
    transfer_text = " ".join(
        _clean_text(value)
        for col in ["出库类型", "业务场景", "备注"]
        for value in group.get(col, pd.Series(dtype=object))
        if _clean_text(value)
    )
    if _clean_text(transfer_target) or any(keyword in transfer_text for keyword in ["调拨", "仓间", "调入"]):
        target = _clean_text(transfer_target)
        if target:
            return {"对象类型": "其他", "平台": "联宇盈仓", "仓点代码": target}, ""

    products = [
        _clean_text(value).upper()
        for value in group.get("FBA/FBX", pd.Series(dtype=object))
        if _clean_text(value).upper() in {"FBA", "FBX"}
    ]
    product_values = list(dict.fromkeys(products))
    if len(product_values) > 1:
        return None, "同一批次同时出现FBA和FBX目的地"
    product = product_values[0] if product_values else ""

    if product == "FBA":
        codes = []
        for value in group.get("FBA仓点代码", pd.Series(dtype=object)):
            codes.extend(split_values(value))
        codes = list(dict.fromkeys(_clean_text(code).upper() for code in codes if _clean_text(code)))
        if not codes:
            raw_destination = first_nonblank(group.get("目的地", pd.Series(dtype=object)))
            if _clean_text(raw_destination):
                return {"对象类型": "FBA", "平台": "FBA", "仓点代码": _clean_text(raw_destination)}, ""
            return None, "缺少FBA目的仓点"
        if len(codes) != 1:
            return None, f"同一批次出现多个FBA目的仓点：{','.join(codes)}"
        return {"对象类型": "FBA", "平台": "FBA", "仓点代码": codes[0]}, ""

    if product == "FBX":
        codes = []
        platforms = []
        for value in group.get("FBX代码", pd.Series(dtype=object)):
            codes.extend(split_values(value))
        for value in group.get("平台名称", pd.Series(dtype=object)):
            platforms.extend(split_values(value))
        codes = list(dict.fromkeys(_clean_text(code) for code in codes if _clean_text(code)))
        platforms = list(dict.fromkeys(
            _clean_text(platform) for platform in platforms
            if _clean_text(platform) and _clean_text(platform) != "非平台/未知"
        ))
        if not codes:
            raw_destination = first_nonblank(group.get("目的地", pd.Series(dtype=object)))
            if _clean_text(raw_destination):
                return {"对象类型": "其他", "平台": platforms[0] if platforms else "", "仓点代码": _clean_text(raw_destination)}, ""
            return None, "缺少FBX目的仓点"
        if len(codes) != 1:
            return None, f"同一批次出现多个FBX目的仓点：{','.join(codes)}"
        if len(platforms) > 1:
            return None, f"同一批次出现多个FBX平台：{','.join(platforms)}"
        return {"对象类型": "FBX平台仓", "平台": platforms[0] if platforms else "", "仓点代码": codes[0]}, ""

    # 调拨或普通商业地址没有FBA/FBX代码时，调入仓、明确目的地或有效邮编均可作为目的地证据。
    evidence = []
    for col in ["调入仓库", "目的地", "标准地址", "标准邮编", "目的州"]:
        if col in group.columns:
            evidence.extend(_clean_text(value) for value in group[col] if _clean_text(value))
    if evidence:
        return {"对象类型": "其他", "平台": "", "仓点代码": evidence[0]}, ""
    return None, "缺少目的地"


def _destination_allocation_json(destination, batch_no, volume, pallets, cost):
    if not destination:
        return ""
    item = {
        "对象类型": destination["对象类型"],
        "平台": destination["平台"],
        "仓点代码": destination["仓点代码"],
        "批次号": batch_no,
        "出库体积": float(volume),
        "出库卡板数": float(pallets),
        "派送成本": float(cost),
    }
    return json.dumps([item], ensure_ascii=False, separators=(",", ":"))


def build_cleaned_batches_from_detail(valid_detail):
    """把派送一整理成一行一个批次，并把车次只作为批次的共享上下文。

    批次字段（目的地、方数、成本、出库/签收时间）绝不再被整车汇总值覆盖。
    同一真实车次只负责统一最终运输类型、车型/装车方式、整车总量和批次车份额。
    """
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
    for col in ["标准运输类型", "车次号", "派送卡车", "批次号", "仓库", "出库类型", "业务场景", "系统产品类型", "FBA/FBX", "平台名称", "FBX代码", "标准邮编", "邮编前三位", "目的州", "FBA仓点代码", "装车类型", "车型", "装车类型标准值", "车型标准值", "调入仓库", "邮编来源", "备注", "目的地", "标准地址"]:
        if col not in df.columns:
            df[col] = ""
    if "原始行号" not in df.columns:
        df["原始行号"] = np.arange(2, len(df) + 2)

    df = processors.apply_trip_transport_type_rules(df)
    rows = []
    df["_批次聚合键"] = df.apply(
        lambda row: (
            f"{_clean_text(row.get('仓库')).upper()}||{_clean_text(row.get('批次号'))}"
            if _clean_text(row.get("批次号"))
            else f"ROW||{_clean_text(row.get('仓库')).upper()}||{row.get('原始行号')}"
        ),
        axis=1,
    )
    for batch_key, group in df.groupby("_批次聚合键", dropna=False, sort=False):
        batch_no = first_nonblank(group["批次号"])
        warehouse = processors.standardize_warehouse(first_nonblank(group["仓库"]))
        transport_type = str(first_nonblank(group["标准运输类型"])).upper().strip()
        transport_type = transport_type if transport_type in {"FTL", "LTL"} else "LTL"
        volume = float(group["出库体积"].sum())
        pallets = float(group["出库卡板数"].sum())
        cost, cost_audit = _batch_cost_value(group["派送成本"])
        delivery_truck, supplier_audit = _batch_delivery_truck(group["派送卡车"])
        destination, destination_error = _batch_destination(group)

        product_values = [
            _clean_text(value).upper()
            for value in group["FBA/FBX"]
            if _clean_text(value).upper() in {"FBA", "FBX"}
        ]
        product_group = list(dict.fromkeys(product_values))[0] if len(set(product_values)) == 1 else ""

        vehicle = resolve_group_vehicle(processors.original_or_standard_group_values(
            group, "车型", "车型标准值", lambda value: processors.normalize_vehicle_type(value, "FTL")[0]
        ))
        loading = resolve_group_loading(processors.original_or_standard_group_values(
            group, "装车类型", "装车类型标准值", lambda value: processors.normalize_loading_type(value, "FTL")[0]
        ))
        if transport_type == "LTL":
            vehicle, loading = "不适用", "散板"
        elif ("53" in vehicle or "大车" in vehicle) and loading not in {"卡板", "地板"}:
            loading = "卡板"

        start_time = group["出库时间"].min()
        end_time = group["签收时间"].max()
        duration = (
            (end_time - start_time).total_seconds() / 86400
            if pd.notna(start_time) and pd.notna(end_time)
            else np.nan
        )
        invalid_reasons = []
        if destination_error:
            invalid_reasons.append(destination_error)
        if volume <= 0:
            invalid_reasons.append("缺少或无效出库体积")
        trip_no = _clean_text(first_nonblank(group["车次号"]))
        fba_code = destination["仓点代码"] if destination and destination["对象类型"] == "FBA" else ""
        fbx_code = destination["仓点代码"] if destination and destination["对象类型"] == "FBX平台仓" else ""
        platform = destination["平台"] if destination and destination["对象类型"] == "FBX平台仓" else ""
        allocation_json = _destination_allocation_json(destination, batch_no, volume, pallets, cost)

        rows.append({
            "分析批次ID": f"BATCH_{batch_key}",
            "仓库": warehouse,
            "标准运输类型": transport_type,
            "原始运输类型集合": combine_unique(group["原始运输类型集合"]),
            "运输类型重判原因": first_nonblank(group["运输类型重判原因"]),
            "派送方式": build_method(transport_type, vehicle, loading),
            "车型标准值": vehicle,
            "装车类型标准值": loading,
            "车次号": trip_no,
            "是否有真实车次号": bool(trip_no),
            "批次号": batch_no,
            "批次号集合": batch_no,
            "原始行号集合": combine_unique(group["原始行号"]),
            "出库类型": first_nonblank(group["出库类型"]),
            "业务场景": first_nonblank(group["业务场景"]),
            "调入仓库": first_nonblank(group["调入仓库"]),
            "批次出库时间": start_time,
            "批次签收时间": end_time,
            "派送时效": duration,
            "出库体积": volume,
            "出库卡板数": pallets,
            "派送成本": cost,
            "批次成本审核": cost_audit,
            "派送卡车": delivery_truck,
            "供应商审核": supplier_audit,
            "FBA出库体积": volume if product_group == "FBA" else 0.0,
            "FBX出库体积": volume if product_group == "FBX" else 0.0,
            "系统产品类型": product_group or first_nonblank(group["系统产品类型"]),
            "主产品类型": product_group or "未知",
            "平台名称": platform,
            "FBX代码集合": fbx_code,
            "平台仓代码集合": fbx_code,
            "平台仓配对集合": f"{platform}||{fbx_code}" if platform and fbx_code else "",
            "FBA仓点代码集合": fba_code,
            "标准邮编集合": combine_unique(group["标准邮编"]),
            "邮编前三位集合": combine_unique(group["邮编前三位"]),
            "目的州": combine_unique(group["目的州"]),
            "邮编来源": combine_unique(group["邮编来源"]),
            "目的仓点分配明细": allocation_json,
            "批次目的地类型": destination["对象类型"] if destination else "",
            "批次目的仓点": destination["仓点代码"] if destination else "",
            "是否混合目的地": False,
            "是否混装": False,
            "批次数据是否有效": not invalid_reasons,
            "批次无效原因": "; ".join(invalid_reasons),
            "备注": combine_unique(group["备注"]),
        })

    all_batches = pd.DataFrame(rows)
    if all_batches.empty:
        return all_batches
    valid_mask = all_batches["批次数据是否有效"].astype(bool)
    invalid_batches = all_batches.loc[~valid_mask].copy()
    result = all_batches.loc[valid_mask].copy()
    if result.empty:
        result.attrs["batch_invalid_records"] = invalid_batches.to_dict("records")
        return result

    # 车次只提供共享上下文。份额分母只使用同车次有效批次，避免无效目的地/方数污染分配。
    result["批次车份额"] = np.nan
    result["整车出库体积"] = np.nan
    result["整车出库卡板数"] = np.nan
    result["整车批次数"] = pd.NA
    result["车次份额合计"] = np.nan
    real_ftl = result["标准运输类型"].eq("FTL") & result["是否有真实车次号"].astype(bool)
    result["_车次聚合键"] = (
        result["仓库"].astype(str).str.upper().str.strip()
        + "||"
        + result["车次号"].astype(str).str.strip()
    )
    for _, indexes in result.loc[real_ftl].groupby("_车次聚合键", dropna=False).groups.items():
        group = result.loc[indexes]
        total_volume = pd.to_numeric(group["出库体积"], errors="coerce").fillna(0).sum()
        total_pallets = pd.to_numeric(group["出库卡板数"], errors="coerce").fillna(0).sum()
        vehicle = resolve_group_vehicle(group["车型标准值"])
        loading = resolve_group_loading(group["装车类型标准值"])
        if ("53" in vehicle or "大车" in vehicle) and loading not in {"卡板", "地板"}:
            loading = "卡板"
        result.loc[indexes, "车型标准值"] = vehicle
        result.loc[indexes, "装车类型标准值"] = loading
        result.loc[indexes, "派送方式"] = build_method("FTL", vehicle, loading)
        result.loc[indexes, "整车出库体积"] = float(total_volume)
        result.loc[indexes, "整车出库卡板数"] = float(total_pallets)
        result.loc[indexes, "整车批次数"] = int(len(group))
        if total_volume > 0:
            shares = pd.to_numeric(group["出库体积"], errors="coerce").fillna(0) / float(total_volume)
            result.loc[indexes, "批次车份额"] = shares.values
            result.loc[indexes, "车次份额合计"] = float(shares.sum())

    result["批次出库时间"] = pd.to_datetime(result["批次出库时间"], errors="coerce")
    result["批次签收时间"] = pd.to_datetime(result["批次签收时间"], errors="coerce")
    result["派送时效"] = pd.to_numeric(result["派送时效"], errors="coerce")
    result["是否有效时效"] = result["批次出库时间"].notna() & result["批次签收时间"].notna() & result["派送时效"].notna() & (result["派送时效"] > 0) & (result["派送时效"] <= 30)
    result.loc[~result["是否有效时效"], "派送时效"] = np.nan
    result["目的地邮编待补充"] = result["标准邮编集合"].apply(lambda x: len(split_values(x)) == 0)
    result = result.drop(columns=["_车次聚合键"], errors="ignore")
    result = result[[col for col in result.columns if col != "备注"] + ["备注"]]
    result = sort_unmatched_zip_bottom(result)
    result.attrs["batch_invalid_records"] = invalid_batches.to_dict("records")
    return result


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
    detail_df, _, _ = processors.process_delivery_stage1_from_files(file_dfs=file_dfs, warehouse=warehouse, period_type="按周统计", start_date=start_date, end_date=end_date)
    detail_df = delivery_reference.apply_delivery_reference_memory(detail_df)
    valid_detail, invalid_detail = remove_invalid_stage1_rows(detail_df)
    cleaned_batches = build_cleaned_batches_from_detail(valid_detail)
    batch_invalid = pd.DataFrame(cleaned_batches.attrs.get("batch_invalid_records", []))
    cleaned_batches.attrs = {}
    if not batch_invalid.empty:
        batch_invalid["无效批次剔除原因"] = batch_invalid["批次无效原因"]
        invalid_detail = pd.concat([invalid_detail, batch_invalid], ignore_index=True, sort=False)
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


def build_trip_audit(cleaned_batches):
    """一行一个真实车次，仅供核对整车构成和批次车份额，不反写批次目的地/时效。"""
    columns = [
        "仓库", "车次号", "最终运输类型", "车型标准值", "装车类型标准值",
        "整车批次数", "整车出库体积", "整车出库卡板数", "批次车份额合计",
        "目的仓点构成", "批次号集合", "车次份额校验", "运输类型重判原因",
    ]
    if cleaned_batches is None or cleaned_batches.empty:
        return pd.DataFrame(columns=columns)
    source = cleaned_batches.copy()
    if "车次号" not in source.columns:
        return pd.DataFrame(columns=columns)
    source = source[source["车次号"].fillna("").astype(str).str.strip().ne("")].copy()
    if source.empty:
        return pd.DataFrame(columns=columns)

    rows = []
    for (warehouse, trip_no), group in source.groupby(["仓库", "车次号"], dropna=False, sort=False):
        final_types = list(dict.fromkeys(group["标准运输类型"].fillna("").astype(str)))
        final_type = final_types[0] if len(final_types) == 1 else ",".join(final_types)
        total_volume = float(pd.to_numeric(group["出库体积"], errors="coerce").fillna(0).sum())
        total_pallets = float(pd.to_numeric(group["出库卡板数"], errors="coerce").fillna(0).sum())
        share_total = pd.to_numeric(group.get("批次车份额", pd.Series(np.nan, index=group.index)), errors="coerce").sum(min_count=1)
        share_valid = final_type != "FTL" or (pd.notna(share_total) and abs(float(share_total) - 1.0) <= 1e-9)
        rows.append({
            "仓库": warehouse,
            "车次号": trip_no,
            "最终运输类型": final_type,
            "车型标准值": first_nonblank(group["车型标准值"]),
            "装车类型标准值": first_nonblank(group["装车类型标准值"]),
            "整车批次数": int(len(group)),
            "整车出库体积": total_volume,
            "整车出库卡板数": total_pallets,
            "批次车份额合计": share_total,
            "目的仓点构成": combine_unique(group.get("批次目的仓点", pd.Series(dtype=object))),
            "批次号集合": combine_unique(group.get("批次号集合", pd.Series(dtype=object))),
            "车次份额校验": "通过" if share_valid else "异常：FTL批次车份额合计不等于1",
            "运输类型重判原因": combine_unique(group.get("运输类型重判原因", pd.Series(dtype=object))),
        })
    return pd.DataFrame(rows, columns=columns)


def read_stage1_cleaned_batches(excel_file):
    excel_file.seek(0)
    xls = pd.ExcelFile(excel_file)
    sheet_name = "清洗后数据" if "清洗后数据" in xls.sheet_names else ("派送一_清洗合并数据" if "派送一_清洗合并数据" in xls.sheet_names else xls.sheet_names[0])
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
        rows.append({"批次号": str(row.get("批次号", "")).strip(), "补充标准邮编": zip_code, "补充目的州": str(row.get("目的州", "")).upper().strip(), "补充邮编修正类型": fix, "补充邮编是否有效": valid, "补充邮编异常原因": reason})
    return pd.DataFrame(rows).drop_duplicates(subset=["批次号"], keep="last")


def apply_manual_match_to_cleaned_batches(cleaned_batches, match_df):
    df = cleaned_batches.copy()
    match = prepare_manual_match(match_df)
    if df.empty or match.empty:
        return df
    match_map = match.set_index("批次号").to_dict("index")
    for idx, row in df.iterrows():
        if split_values(row.get("标准邮编集合", "")):
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
    cleaned_input = cleaned_batches.copy()
    cleaned_input.attrs = {}
    matched = apply_manual_match_to_cleaned_batches(cleaned_input, match_df)
    matched = apply_linehaul_rules_second_part(matched)
    matched = apply_region_rules_second_part(matched)
    matched = add_analysis_period(matched, period_type)
    for col in ["出库体积", "出库卡板数", "派送成本", "FBA出库体积", "FBX出库体积", "派送时效"]:
        if col not in matched.columns:
            matched[col] = 0
        matched[col] = pd.to_numeric(matched[col], errors="coerce").fillna(0)
    real_trip = matched.get("是否有真实车次号", matched.get("车次号", pd.Series("", index=matched.index)))
    if isinstance(real_trip, pd.Series):
        real_trip = real_trip.apply(
            lambda value: (
                value
                if isinstance(value, (bool, np.bool_))
                else _clean_text(value).lower() in {"true", "1", "是", "yes", "y"}
            )
        )
        if "是否有真实车次号" not in matched.columns:
            real_trip = matched.get("车次号", pd.Series("", index=matched.index)).apply(lambda value: bool(_clean_text(value)))
    matched["是否有真实车次号"] = real_trip.astype(bool)
    if "批次车份额" in matched.columns:
        share = pd.to_numeric(matched["批次车份额"], errors="coerce")
    else:
        # 兼容旧版派送一“一行一整车”文件；新文件始终携带精确批次车份额。
        share = pd.Series(
            np.where(matched["标准运输类型"].eq("FTL") & matched["是否有真实车次号"], 1.0, np.nan),
            index=matched.index,
        )
        matched["批次车份额"] = share
    matched["是否FTL发车"] = matched["标准运输类型"].eq("FTL") & matched["是否有真实车次号"] & share.gt(0)
    matched["主产品类型"] = matched.apply(main_product_for_dispatch, axis=1)
    remark_cols = [col for col in processors.MULTI_UNLOAD_REMARK_COLUMNS if col in matched.columns and col != "同车次备注集合"]
    if "同车次备注集合" in matched.columns:
        remark_cols.insert(0, "同车次备注集合")
    if remark_cols:
        matched["同车次备注集合"] = matched[remark_cols].apply(lambda row: combine_unique(row.tolist()), axis=1)
    else:
        matched["同车次备注集合"] = ""
    matched = matched[[col for col in matched.columns if col != "同车次备注集合"] + ["同车次备注集合"]]
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
    if df.empty:
        return df.iloc[0:0].copy()
    if "专线线路" not in df.columns:
        out = df.iloc[0:0].copy()
        out["专线线路"] = pd.Series(dtype=object)
        return out
    return df[(df["仓库"] == "LA") & (~df["专线线路"].isin(["", "未知线路", "非LA干线"]))].copy()


def rank_top_bottom(df, group_col, value_col, top_n=10, bottom_n=10):
    agg = df.groupby(group_col, dropna=False)[value_col].sum().reset_index()
    agg = agg[~agg[group_col].apply(processors.is_blank)]
    agg = agg[agg[value_col] > 0].sort_values(value_col, ascending=False)
    if agg.empty:
        return agg
    top = agg.head(top_n).copy(); top["排行类型"] = f"前{top_n}"
    bottom = agg.tail(bottom_n).copy().sort_values(value_col, ascending=True); bottom["排行类型"] = f"后{bottom_n}"
    return pd.concat([top, bottom], ignore_index=True)


def business_round_vehicle_count(value):
    value = pd.to_numeric(value, errors="coerce")
    if pd.isna(value) or float(value) <= 0:
        return 0
    return int(np.floor(float(value) + 0.5))


def volume_weighted_average(df, value_col="派送时效", weight_col="出库体积"):
    if df is None or df.empty:
        return np.nan
    values = pd.to_numeric(df[value_col], errors="coerce")
    weights = pd.to_numeric(df[weight_col], errors="coerce")
    valid = values.notna() & weights.gt(0)
    if not valid.any():
        return np.nan
    return float((values[valid] * weights[valid]).sum() / weights[valid].sum())


def volume_weighted_p80(df, value_col="派送时效", weight_col="出库体积"):
    """按方数权重求离散P80：累计有效方数首次达到80%时对应的批次时效。"""
    if df is None or df.empty:
        return np.nan
    sample = pd.DataFrame({
        "value": pd.to_numeric(df[value_col], errors="coerce"),
        "weight": pd.to_numeric(df[weight_col], errors="coerce"),
    }).dropna()
    sample = sample[sample["weight"] > 0].sort_values("value", kind="stable")
    if sample.empty:
        return np.nan
    cutoff = float(sample["weight"].sum()) * 0.8
    cumulative = sample["weight"].cumsum()
    return float(sample.loc[cumulative.ge(cutoff), "value"].iloc[0])


def timing_sample_rows(df):
    """时效只看有真实车次的最终FTL批次；LTL、缺车次、无效时效及里/外备注均排除。"""
    if df is None or df.empty:
        return df.copy()
    out = df.copy()
    if "标准运输类型" in out.columns:
        mask = out["标准运输类型"].astype(str).str.upper().eq("FTL")
    else:
        mask = out.get("是否FTL发车", pd.Series(False, index=out.index)).apply(
            lambda value: value is True or str(value).strip().lower() in {"true", "1", "是", "yes"}
        )
    if "是否有真实车次号" in out.columns:
        has_trip = out["是否有真实车次号"].apply(
            lambda value: value is True or str(value).strip().lower() in {"true", "1", "是", "yes"}
        )
    else:
        has_trip = out.get("车次号", pd.Series("", index=out.index)).apply(lambda value: bool(_clean_text(value)))
    mask &= has_trip
    duration = pd.to_numeric(out.get("派送时效", pd.Series(np.nan, index=out.index)), errors="coerce")
    volume = pd.to_numeric(out.get("出库体积", pd.Series(np.nan, index=out.index)), errors="coerce")
    mask &= duration.notna() & duration.gt(0) & volume.gt(0)
    remark_cols = [col for col in processors.MULTI_UNLOAD_REMARK_COLUMNS if col in out.columns]
    if remark_cols:
        remarks = out[remark_cols].fillna("").astype(str).agg(" ".join, axis=1)
        mask &= ~remarks.str.contains("里|外", regex=True, na=False)
    return out.loc[mask].copy()


def _append_weighted_timing_row(rows, metric_name, warehouse, period, dimension_type, dimension_value, group):
    sample = timing_sample_rows(group)
    row = report_row(
        "3.派送时效",
        metric_name,
        warehouse,
        period,
        dimension_type,
        dimension_value,
        avg_time=volume_weighted_average(sample),
        p80_time=volume_weighted_p80(sample),
        note="按有效FTL批次方数加权；LTL、缺车次、无效时效及备注含‘里/外’批次不参与",
    )
    row["有效时效批次数"] = int(len(sample))
    row["有效时效方数"] = float(pd.to_numeric(sample.get("出库体积", 0), errors="coerce").fillna(0).sum()) if not sample.empty else 0.0
    row["无效时效批次数"] = int(len(group) - len(sample))
    rows.append(row)


def build_sheet1_volume_dispatch_time_report(df):
    rows = []
    if df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["货量结构"] = df.apply(volume_structure_label, axis=1)
    for (warehouse, period), group in df.groupby(["仓库", "统计周期"], dropna=False):
        ltl_volume = group.loc[group["标准运输类型"] == "LTL", "出库体积"].sum()
        non_ltl_volume = group.loc[group["标准运输类型"] != "LTL", "出库体积"].sum()
        add_ratio_rows(rows, "1.货量", "非LTL方数比LTL方数", warehouse, period, "运输类型", {"非LTL": non_ltl_volume, "LTL": ltl_volume}, "CBM")
        structure = group.groupby("货量结构")["出库体积"].sum().to_dict()
        ordered = {k: structure.get(k, 0) for k in ["卡板", "地板", "LTL"]}
        for k, v in structure.items():
            if k not in ordered:
                ordered[k] = v
        add_ratio_rows(rows, "1.货量", "卡板比地板比LTL方数", warehouse, period, "货量结构", ordered, "CBM")
        add_ratio_rows(rows, "1.货量", "FBA比FBX方数", warehouse, period, "产品类型", {"FBA": group["FBA出库体积"].sum(), "FBX": group["FBX出库体积"].sum()}, "CBM")
        fba_rank_source = group[group["FBA出库体积"] > 0].copy()
        if not fba_rank_source.empty:
            for _, r in rank_top_bottom(fba_rank_source, "FBA仓点代码集合", "FBA出库体积").iterrows():
                rows.append(report_row("1.货量", "FBA仓点货量排行", warehouse, period, r.get("排行类型", "排行"), r["FBA仓点代码集合"], value=r["FBA出库体积"], unit="CBM", volume=r["FBA出库体积"]))
        platform_source = group[(group["FBX出库体积"] > 0) & (~group["平台名称"].apply(processors.is_blank)) & (group["平台名称"] != "非平台/未知")].copy()
        if not platform_source.empty:
            platform_rank = platform_source.groupby("平台名称", dropna=False)["FBX出库体积"].sum().reset_index()
            platform_rank = platform_rank[platform_rank["FBX出库体积"] > 0].sort_values("FBX出库体积", ascending=False)
            for _, r in platform_rank.iterrows():
                rows.append(report_row("1.货量", "FBX平台仓货量排行", warehouse, period, "平台", r["平台名称"], value=r["FBX出库体积"], unit="CBM", volume=r["FBX出库体积"]))
        if warehouse == "LA":
            lh = linehaul_df(group)
            for line, lg in lh.groupby("专线线路", dropna=False):
                rows.append(report_row("1.货量", "LA干线货量", warehouse, period, "干线线路", line, value=lg["出库体积"].sum(), unit="CBM", volume=lg["出库体积"].sum()))
        ftl_group = group[group["是否FTL发车"]].copy()
        ftl_group["批次车份额"] = pd.to_numeric(ftl_group["批次车份额"], errors="coerce").fillna(0)
        unique_trip_count = ftl_group["车次号"].dropna().astype(str).replace("", np.nan).dropna().nunique()
        total_dispatch_row = report_row(
            "2.发车量", "总发车数", warehouse, period, "发车口径", "FTL实际车次",
            value=int(unique_trip_count), unit="车", dispatch=int(unique_trip_count),
            note="总量按真实车次号去重；LTL及缺车次批次不计入发车数",
        )
        rows.append(total_dispatch_row)
        loading_exact = ftl_group.groupby("装车类型标准值", dropna=False)["批次车份额"].sum().to_dict()
        loading_display = {
            "地板": business_round_vehicle_count(loading_exact.get("地板", 0)),
            "卡板": business_round_vehicle_count(loading_exact.get("卡板", 0)),
        }
        add_ratio_rows(rows, "2.发车量", "地板发车比卡板发车", warehouse, period, "装车类型", loading_display, "车")
        product_exact = ftl_group.groupby("主产品类型", dropna=False)["批次车份额"].sum().to_dict()
        product_display = {
            "FBA": business_round_vehicle_count(product_exact.get("FBA", 0)),
            "FBX": business_round_vehicle_count(product_exact.get("FBX", 0)),
        }
        add_ratio_rows(rows, "2.发车量", "FBA比FBX发车", warehouse, period, "产品类型", product_display, "车")
        for region, rg in ftl_group.groupby("派送区域", dropna=False):
            exact_count = float(rg["批次车份额"].sum())
            count = business_round_vehicle_count(exact_count)
            row = report_row("2.发车量", "区域发车数", warehouse, period, "派送区域", region, value=count, unit="车", dispatch=count)
            row["精确车份额"] = exact_count
            row["备注"] = "批次车份额汇总后按四舍五入取整；不逐批次取整"
            rows.append(row)
        for station, sg in ftl_group.groupby("批次目的仓点", dropna=False):
            if processors.is_blank(station):
                continue
            exact_count = float(sg["批次车份额"].sum())
            count = business_round_vehicle_count(exact_count)
            row = report_row("2.发车量", "目的仓点发车数", warehouse, period, "目的仓点", station, value=count, unit="车", dispatch=count)
            row["精确车份额"] = exact_count
            row["备注"] = "批次车份额汇总后按四舍五入取整；不逐批次取整"
            rows.append(row)
        if warehouse == "LA":
            lh_ftl = linehaul_df(ftl_group)
            for line, lg in lh_ftl.groupby("专线线路", dropna=False):
                exact_count = float(lg["批次车份额"].sum())
                count = business_round_vehicle_count(exact_count)
                row = report_row("2.发车量", "LA干线发车数", warehouse, period, "干线线路", line, value=count, unit="车", dispatch=count)
                row["精确车份额"] = exact_count
                row["备注"] = "批次车份额汇总后按四舍五入取整；不逐批次取整"
                rows.append(row)
        for region, rg in group.groupby("派送区域", dropna=False):
            _append_weighted_timing_row(rows, "分区域派送时效", warehouse, period, "派送区域", region, rg)
        for station, sg in group.groupby("批次目的仓点", dropna=False):
            if not processors.is_blank(station):
                _append_weighted_timing_row(rows, "目的仓点派送时效", warehouse, period, "目的仓点", station, sg)
        if warehouse == "LA":
            lh_time = linehaul_df(group)
            for line, lg in lh_time.groupby("专线线路", dropna=False):
                _append_weighted_timing_row(rows, "LA干线派送时效", warehouse, period, "干线线路", line, lg)
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
        return pd.DataFrame()
    ftl["车型装车分组"] = ftl.apply(cost_vehicle_group, axis=1)
    ftl[["对象类型", "对象名称"]] = ftl.apply(lambda r: pd.Series(cost_dimension_label(r)), axis=1)
    full_load = ftl[(ftl["车型标准值"] == "53尺大车") & (ftl["装车类型标准值"] == "地板")].copy()
    for (warehouse, period), group in full_load.groupby(["仓库", "统计周期"], dropna=False):
        rows.append({"报告部分": "4.成本", "指标名称": "满载情况", "仓库": warehouse, "统计周期": period, "对象类型": "FTL大车地板", "对象名称": "全部", "车型装车分组": "大车地板", "车次数": len(group), "总出库体积": group["出库体积"].sum(), "总派送成本": group["派送成本"].sum(), "平均整车价": np.nan, "每方平均价": np.nan, "平均每车出库体积": group["出库体积"].mean(), "P80每车出库体积": processors.safe_p80(group["出库体积"]), "备注": "满载口径：FTL + 53尺大车 + 地板"})
    cost_source = ftl[ftl["对象类型"].isin(["FBA", "FBX平台仓"])].copy()
    cost_source = cost_source[cost_source["车型装车分组"].isin(["小车", "大车卡板", "大车地板"])]
    for (warehouse, period, obj_type, obj_name, vehicle_group), group in cost_source.groupby(["仓库", "统计周期", "对象类型", "对象名称", "车型装车分组"], dropna=False):
        total_cost = group["派送成本"].sum(); total_volume = group["出库体积"].sum()
        rows.append({"报告部分": "4.成本", "指标名称": "FBA及FBX平台仓成本", "仓库": warehouse, "统计周期": period, "对象类型": obj_type, "对象名称": obj_name, "车型装车分组": vehicle_group, "车次数": len(group), "总出库体积": total_volume, "总派送成本": total_cost, "平均整车价": group["派送成本"].mean(), "每方平均价": processors.safe_divide(total_cost, total_volume), "平均每车出库体积": group["出库体积"].mean(), "P80每车出库体积": processors.safe_p80(group["出库体积"]), "备注": "小车不区分卡板/地板；大车区分卡板与地板"})
    return pd.DataFrame(rows)


def dispatch_rows(df):
    return df[df["是否FTL发车"]].copy()


def process_stage2_analysis(cleaned_batches, match_df, period_type="按周统计"):
    matched = prepare_stage2_for_report(cleaned_batches, match_df, period_type)
    sheet1 = build_sheet1_volume_dispatch_time_report(matched)
    sheet2 = build_sheet2_cost_report(matched)
    return {
        "表一_货量发车时效": processors.round_output_numbers(sheet1, processors.RESULT_DECIMALS),
        "表二_成本": processors.round_output_numbers(sheet2, processors.RESULT_DECIMALS),
        "派送二_匹配后批次数据": processors.round_output_numbers(matched, processors.RESULT_DECIMALS),
        "派送二_车次汇总核对": processors.round_output_numbers(build_trip_audit(matched), processors.RESULT_DECIMALS),
        "邮编异常审核": matched[matched["目的地邮编待补充"]].copy() if "目的地邮编待补充" in matched.columns else pd.DataFrame(),
        "区域识别规则": REGION_RULES_DF,
        "干线识别规则": LINEHAUL_RULES,
        "内置FBA邮编表": delivery_reference.FBA_REFERENCE_DF,
        "内置平台仓邮编表": delivery_reference.PLATFORM_REFERENCE_DF,
    }

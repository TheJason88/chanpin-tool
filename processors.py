import re
import numpy as np
import pandas as pd


# =========================
# 1. 基础配置
# =========================

VALID_WAREHOUSES = ["LA", "NJ", "SAV", "DAL"]

WAREHOUSE_MAP = {
    "美西二号仓": "LA", "美西仓": "LA", "洛杉矶": "LA", "CA": "LA", "LA": "LA", "LAX": "LA",
    "达拉斯盈仓": "DAL", "达拉斯": "DAL", "DAL": "DAL", "Dallas": "DAL",
    "萨凡纳盈仓": "SAV", "萨凡纳": "SAV", "SAV": "SAV", "Savannah": "SAV",
    "新泽西二号仓": "NJ", "新泽西": "NJ", "NJ": "NJ", "New Jersey": "NJ",
}

TRANSFER_WAREHOUSE_TO_LINE = {
    "新泽西二号仓": "LA-NJ", "新泽西": "LA-NJ", "NJ": "LA-NJ",
    "达拉斯盈仓": "LA-DAL", "达拉斯": "LA-DAL", "DAL": "LA-DAL",
    "萨凡纳盈仓": "LA-SAV", "萨凡纳": "LA-SAV", "SAV": "LA-SAV",
    "芝加哥": "LA-CHI", "芝加哥仓": "LA-CHI", "CHI": "LA-CHI",
}

FIELD_ALIASES = {
    "仓库": [
        "仓库", "仓点", "仓库名称", "所属仓", "目的仓", "发货仓", "发货仓库",
        "入库仓库", "入库仓", "到仓仓库", "抵仓仓库", "实际入库仓库",
        "Warehouse", "Inbound Warehouse", "Origin Warehouse"
    ],
    "客户名称": ["客户名称", "客户", "客户名", "客户公司", "Customer", "Customer Name"],
    "产品渠道": ["产品渠道", "渠道", "T渠道", "服务渠道", "产品通道"],
    "ETA": ["ETA", "eta", "预计到仓时间", "预计抵仓时间", "预计到港时间", "预计到达时间"],

    "出库时间": ["出库时间", "实际出库时间", "发车时间", "Outbound Time", "Ship Time"],
    "签收时间": ["签收时间", "实际签收时间", "POD时间", "妥投时间", "Delivered Time", "Delivery Time"],
    "目的地": ["目的地", "目的地址", "派送地址", "收货地址", "Destination"],
    "转仓地址": ["转仓地址", "中转地址", "转运地址", "Transfer Address"],
    "派送成本": ["派送成本", "成本", "Delivery Cost"],
    "车次号": ["车次号", "车次", "派送卡车", "卡车", "批次号", "批次", "Load No", "Trip No"],
    "派送卡车": ["派送卡车", "卡车", "Truck"],
    "批次号": ["批次号", "批次", "工作单号", "工作单", "订单号", "SO", "SO号"],
    "派送方式": ["派送方式", "配送方式", "Delivery Method"],
    "出库类型": ["出库类型", "业务类型", "Outbound Type"],
    "批次状态": ["批次状态", "状态", "Batch Status"],
    "运输类型": ["运输类型", "运输方式", "Transport Type", "Transportation Type"],
    "车型": ["车型", "车辆类型", "Truck Type", "Vehicle Type"],
    "装车类型": ["装车类型", "装载类型", "货型", "货物类型", "货物形态", "Loading Type"],
    "出库体积": ["出库体积", "体积", "方数", "CBM", "Volume"],
    "出库卡板数": ["出库卡板数", "卡板数", "板数", "托盘数", "Pallets"],
    "调入仓库": ["调入仓库", "调入仓", "目的仓库", "调拨目的仓", "Transfer To Warehouse"],
    "备注": ["备注", "Note", "Remark", "Remarks"],
    "创建时间": ["创建时间", "Create Time"],
    "创建人": ["创建人", "Creator"],
    "是否转仓": ["是否转仓", "转仓", "Is Transfer"],
    "是否外配TMS": ["是否外配TMS", "外配TMS"],
    "操作": ["操作", "Operation"],

    "目的地邮编": ["目的地邮编", "邮编", "标准邮编", "ZIP", "Zip", "zipcode", "ZipCode", "Postal Code"],
    "目的州": ["目的州", "州", "到达州", "目的地州", "State", "Destination State"],

    "提柜时间": ["提柜时间", "实际提柜时间", "提柜日期", "Pickup Time", "Pick Up Time"],
    "Available时间": ["Available时间", "AVAILABLE时间", "Available Time", "可提时间", "码头可提时间"],
    "实际抵仓时间": ["实际抵仓时间", "实际到仓时间", "抵仓时间", "到仓时间", "Actual Arrival Time", "Arrival Time"],
    "拆柜完成时间": ["拆柜完成时间", "拆柜结束时间", "拆柜完毕时间", "拆柜完成日期", "Unload Finish Time"],

    "体积": ["体积", "方数", "CBM", "Volume", "出库体积"],
    "卡板数": ["卡板数", "板数", "托盘数", "Pallets", "出库卡板数"],
    "工作单号": ["工作单号", "工作单", "运单号", "订单号", "SO", "SO号"],
    "柜号": ["柜号", "箱号", "Container", "Container No", "ContainerNo", "Container Number"],

    "平台仓点": ["平台仓点", "平台仓", "平台仓库", "仓点名称", "平台仓代码"],
    "平台名称": ["平台名称", "平台", "平台类型"],
}

PLATFORM_KEYWORDS = [
    "Walmart", "WalMart", "TikTok", "TiKToK", "Tiktok", "SHEIN", "Shein", "希音",
    "谷仓", "Wayfair", "万邑通", "运去哪", "乐歌", "盈仓", "TEMU", "Temu",
    "易达云", "橙联", "4PX", "西邮", "苏莱美", "京东仓", "Newegg", "大健云仓"
]

EXCLUDE_FBX_KEYWORDS = [
    "Amazon", "AMAZON", "amazon", "商业地址", "私人地址", "住宅地址", "首页地址私人地址", "首页地址商业地址"
]

LOW_VOLUME_TICKET_THRESHOLD = 2
LOW_VOLUME_SHARE_THRESHOLD = 0.005
RESULT_DECIMALS = 2
CONTAINER_NO_PATTERN = re.compile(r"^[A-Z]{3}[UJZ]\d{7}$")
CHINESE_PATTERN = re.compile(r"[\u4e00-\u9fff]")


# =========================
# 2. 通用清洗函数
# =========================

def clean_col_name(col):
    return str(col).strip().replace("\n", "").replace("\r", "").replace(" ", "").replace("　", "")


def normalize_columns(df):
    df = df.copy()
    df.columns = [clean_col_name(c) for c in df.columns]

    for standard_col, aliases in FIELD_ALIASES.items():
        if standard_col in df.columns:
            continue
        for alias in aliases:
            alias_clean = clean_col_name(alias)
            if alias_clean in df.columns:
                df = df.rename(columns={alias_clean: standard_col})
                break
    return df


def is_blank(value):
    if pd.isna(value):
        return True
    return str(value).strip() in ["", "nan", "NaN", "None", "none", "null", "NULL", "-"]


def has_chinese(value):
    if pd.isna(value):
        return False
    return bool(CHINESE_PATTERN.search(str(value)))


def standardize_warehouse(value):
    if pd.isna(value):
        return np.nan
    value = str(value).strip()
    return WAREHOUSE_MAP.get(value, value)


def contains_any(series, keywords):
    pattern = "|".join(re.escape(k) for k in keywords)
    return series.astype(str).str.contains(pattern, na=False, regex=True)


def ensure_numeric_cols(df, cols):
    df = df.copy()
    for col in cols:
        if col not in df.columns:
            df[col] = 0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    return df


def safe_divide(numerator, denominator):
    if denominator == 0 or pd.isna(denominator):
        return np.nan
    return numerator / denominator


def safe_p80(series):
    clean_series = pd.to_numeric(series, errors="coerce").dropna()
    if clean_series.empty:
        return np.nan
    return clean_series.quantile(0.8)


def safe_p90(series):
    clean_series = pd.to_numeric(series, errors="coerce").dropna()
    if clean_series.empty:
        return np.nan
    return clean_series.quantile(0.9)


MULTI_UNLOAD_REMARK_MARKERS = ("里", "外")
MULTI_UNLOAD_REMARK_COLUMNS = ("匹配备注集合", "备注", "备注信息", "MEMO", "跟进记录", "内部备注")


def average_sample_rows(df):
    """Return detail rows eligible for averages/P80.

    A remark containing either ``里`` or ``外`` marks a batch that shares a
    two-stop trip with a non-transfer batch.  It remains in all totals, but it
    must not contribute an observation to an average or percentile.
    """
    if df is None or df.empty:
        return df.copy()
    remark_cols = [col for col in MULTI_UNLOAD_REMARK_COLUMNS if col in df.columns]
    if not remark_cols:
        return df.copy()
    combined = df[remark_cols].fillna("").astype(str).agg(" ".join, axis=1)
    excluded = combined.str.contains("里|外", regex=True, na=False)
    return df.loc[~excluded].copy()


def mean_detail_ratio(df, numerator_col, denominator_col):
    """Mean of eligible row-level ratios, never a ratio of aggregate totals."""
    if df is None or df.empty:
        return np.nan
    numerator = pd.to_numeric(df[numerator_col], errors="coerce")
    denominator = pd.to_numeric(df[denominator_col], errors="coerce")
    ratios = numerator.div(denominator.where(denominator.ne(0))).replace([np.inf, -np.inf], np.nan).dropna()
    return ratios.mean() if not ratios.empty else np.nan


def normalized_ratio_values(counts, decimals=RESULT_DECIMALS):
    counts = [0 if pd.isna(v) else float(v) for v in counts]
    total = sum(counts)
    if total <= 0:
        return [0.0 for _ in counts]

    raw = [v / total for v in counts]
    rounded = [round(v, decimals) for v in raw]
    if len(rounded) >= 2:
        rounded[-1] = round(1 - sum(rounded[:-1]), decimals)
    return rounded


def format_ratio_values(values):
    return ":".join(f"{float(v):.{RESULT_DECIMALS}f}" for v in values)


def normalize_container_no(value):
    if pd.isna(value):
        return ""
    value = str(value).strip().upper()
    value = re.sub(r"[\s\-　]+", "", value)
    if value in ["", "NAN", "NONE", "NULL", "-"]:
        return ""
    return value


def is_valid_container_no(value):
    return bool(CONTAINER_NO_PATTERN.match(normalize_container_no(value)))


def filter_valid_container_rows(df, module_name):
    df = df.copy()
    require_columns(df, ["柜号"], module_name)
    df["标准柜号"] = df["柜号"].apply(normalize_container_no)
    df["柜号是否有效"] = df["标准柜号"].apply(is_valid_container_no)
    df["柜号异常原因"] = ""
    df.loc[~df["柜号是否有效"], "柜号异常原因"] = "非标准柜号，已剔除"
    return df[df["柜号是否有效"]].copy()


def deduplicate_by_container_no(df, sort_col=None):
    df = df.copy()
    if "标准柜号" not in df.columns:
        df["标准柜号"] = df["柜号"].apply(normalize_container_no)

    if sort_col and sort_col in df.columns:
        df[sort_col] = pd.to_datetime(df[sort_col], errors="coerce")
        df = df.sort_values(sort_col)

    return df.drop_duplicates(subset=["标准柜号"], keep="last").copy()


def filter_date_range(df, date_col, start_date=None, end_date=None):
    df = df.copy()
    if start_date is None and end_date is None:
        return df
    if date_col not in df.columns:
        raise ValueError(f"时间范围筛选缺少日期字段：{date_col}")

    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    if start_date is not None:
        df = df[df[date_col] >= pd.to_datetime(start_date)]
    if end_date is not None:
        df = df[df[date_col] < pd.to_datetime(end_date) + pd.Timedelta(days=1)]
    return df.copy()


def prepare_base_df(df):
    df = normalize_columns(df)
    if "原始行号" not in df.columns:
        df.insert(0, "原始行号", range(2, len(df) + 2))
    if "仓库" in df.columns:
        df["仓库"] = df["仓库"].apply(standardize_warehouse)
    if "产品渠道" not in df.columns:
        df["产品渠道"] = np.nan
    if "客户名称" not in df.columns:
        df["客户名称"] = np.nan
    df["T渠道类型"] = df["产品渠道"].apply(classify_t_channel)
    return df


def filter_warehouse(df, warehouse):
    df = df.copy()
    if warehouse == "四仓合并":
        if "仓库" in df.columns:
            return df[df["仓库"].isin(VALID_WAREHOUSES)].copy()
        df["仓库"] = "未知仓库"
        return df
    if "仓库" in df.columns:
        return df[df["仓库"] == warehouse].copy()
    df["仓库"] = warehouse
    return df


def add_period_column(df, period_type, date_col):
    df = df.copy()
    if date_col not in df.columns:
        raise ValueError(f"缺少日期字段：{date_col}")
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")

    if period_type == "按月统计":
        df["统计周期"] = df[date_col].dt.strftime("%Y-%m")
    else:
        df["周起始日"] = df[date_col] - pd.to_timedelta(df[date_col].dt.weekday, unit="D")
        df["周结束日"] = df["周起始日"] + pd.Timedelta(days=6)
        df["统计周期"] = df["周起始日"].dt.strftime("%Y-%m-%d") + " ~ " + df["周结束日"].dt.strftime("%Y-%m-%d")

    df["统计周期"] = df["统计周期"].fillna("未知周期")
    return df


def require_columns(df, required_cols, module_name):
    missing = [col for col in required_cols if col not in df.columns]
    if missing:
        raise ValueError(f"{module_name}缺少必要字段：{missing}")


def check_product_channel_available(df, module_name):
    if "产品渠道" not in df.columns:
        raise ValueError(f"{module_name}需要字段：产品渠道。该字段用于识别 T1 / T2 / T3。")
    if df["产品渠道"].apply(is_blank).all():
        raise ValueError(f"{module_name}中产品渠道字段全为空，无法按 T1 / T2 / T3 分析。")


def round_output_numbers(df, decimals=RESULT_DECIMALS):
    df = df.copy()
    float_cols = df.select_dtypes(include=["float", "float64", "float32"]).columns
    if len(float_cols) > 0:
        df[float_cols] = df[float_cols].round(decimals)
    return df


# =========================
# 3. 客户类型 / T渠道逻辑
# =========================

def classify_customer_type_for_product_volume(row):
    customer_name = row.get("客户名称", "")
    customer_name = "" if pd.isna(customer_name) else str(customer_name).strip()
    if ("劲港" in customer_name) or ("联宇" in customer_name):
        return "联宇"
    if has_chinese(customer_name):
        return "非联宇"
    return "美国本土客户"


def classify_customer_type_for_volume(row):
    product_channel = row.get("产品渠道", np.nan)
    customer_name = row.get("客户名称", "")
    if is_blank(product_channel):
        return "美国本土客户"
    customer_name = "" if pd.isna(customer_name) else str(customer_name).strip()
    if ("劲港" in customer_name) or ("联宇" in customer_name):
        return "联宇"
    return "非联宇"


def classify_customer_type_for_time_ops(row):
    customer_name = row.get("客户名称", "")
    customer_name = "" if pd.isna(customer_name) else str(customer_name).strip()
    if ("劲港" in customer_name) or ("联宇" in customer_name):
        return "联宇"
    return "非联宇"


def classify_t_channel(value):
    if is_blank(value):
        return "未知渠道"
    text = str(value).upper()
    if "T1" in text:
        return "T1"
    if "T2" in text:
        return "T2"
    if "T3" in text:
        return "T3"
    return "其他渠道"


# =========================
# 4. 目的地 / 产品类型逻辑
# =========================

def is_valid_transfer_address(value):
    if is_blank(value):
        return False
    text = str(value).strip()
    if re.fullmatch(r"\d+", text):
        return False
    if text in ["27", "0", "1"]:
        return False
    if re.search(r"Amazon|商业地址|私人地址|住宅地址", text, flags=re.IGNORECASE):
        return True
    if any(k.lower() in text.lower() for k in PLATFORM_KEYWORDS):
        return True
    if re.search(r"\b\d{5}(?:-\d{4})?\b", text):
        return True
    return False


def apply_transfer_destination(df):
    df = df.copy()
    if "目的地" not in df.columns:
        raise ValueError("缺少目的地字段")

    df["原始目的地"] = df["目的地"]
    if "转仓地址" in df.columns:
        valid_transfer = df["转仓地址"].apply(is_valid_transfer_address)
        df["是否使用转仓地址"] = valid_transfer
        df["转仓审核结果"] = np.where(valid_transfer, "转仓地址有效-已覆盖", "未覆盖")
        df.loc[valid_transfer, "目的地"] = df.loc[valid_transfer, "转仓地址"]
    else:
        df["是否使用转仓地址"] = False
        df["转仓审核结果"] = "无转仓地址字段"
    df["修正后目的地"] = df["目的地"]
    return df


def classify_system_product_type(destination):
    destination = "" if pd.isna(destination) else str(destination)
    if re.search(r"Amazon", destination, flags=re.IGNORECASE):
        return "FBA"
    if any(keyword.lower() in destination.lower() for keyword in PLATFORM_KEYWORDS):
        return "FBX平台仓"
    if any(keyword in destination for keyword in ["商业地址", "私人地址", "住宅地址"]):
        return "FBX非平台地址"
    return "未知"


def classify_delivery_product_group(system_product_type):
    if system_product_type == "FBA":
        return "FBA"
    if str(system_product_type).startswith("FBX"):
        return "FBX"
    return "未知"


def filter_by_product_type(df, product_type):
    df = df.copy()
    if product_type == "全部":
        return df
    if "修正后目的地" not in df.columns:
        if "目的地" in df.columns:
            df["修正后目的地"] = df["目的地"]
        else:
            return df
    destination = df["修正后目的地"].astype(str)
    if product_type == "FBA":
        return df[destination.str.contains("Amazon", na=False, case=False, regex=True)].copy()
    if product_type == "FBX":
        return df[~destination.str.contains("Amazon", na=False, case=False, regex=True)].copy()
    return df


def extract_platform_name(text):
    text = "" if pd.isna(text) else str(text)
    low = text.lower()
    if "tiktok" in low:
        return "TikTok"
    if "shein" in low or "希音" in text:
        return "SHEIN"
    if "walmart" in low:
        return "Walmart"
    if "temu" in low:
        return "TEMU"
    if "4px" in low:
        return "4PX"
    if "newegg" in low:
        return "Newegg"
    for keyword in ["谷仓", "Wayfair", "万邑通", "运去哪", "乐歌", "盈仓", "易达云", "橙联", "西邮", "苏莱美", "京东仓", "大健云仓"]:
        if keyword in text:
            return keyword
    return "非平台/未知"


def extract_fba_code(text):
    text = "" if pd.isna(text) else str(text).strip()
    match = re.search(r"Amazon[-_\s]*([A-Z0-9]+)", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return ""


# =========================
# 5. 邮编与派送规则
# =========================

def normalize_zip_value(value):
    if is_blank(value):
        return "", "邮编缺失", False, "邮编缺失"

    text = str(value).strip()
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]

    zip4 = re.search(r"\b(\d{5})-\d{4}\b", text)
    if zip4:
        return zip4.group(1), "ZIP+4已截取前五位", True, ""

    exact5 = re.search(r"(?<!\d)(\d{5})(?!\d)", text)
    if exact5:
        return exact5.group(1), "无需修正", True, ""

    exact4 = re.search(r"(?<!\d)(\d{4})(?!\d)", text)
    if exact4:
        return "0" + exact4.group(1), "四位邮编已补0", True, ""

    return "", "邮编格式异常", False, "无法识别为4位或5位邮编"


def extract_zip_from_destination(text):
    if is_blank(text):
        return ""
    text = str(text)
    # 只提取被分隔符包围的邮编，避免从商业地址内部长编号中误提取。
    zip_match = re.search(r"(?:^|[^A-Za-z0-9])(\d{5})(?:[^0-9]|$)", text)
    if zip_match:
        return zip_match.group(1)
    zip4_match = re.search(r"(?:^|[^A-Za-z0-9])(\d{4})(?:[^0-9]|$)", text)
    if zip4_match:
        return zip4_match.group(1)
    return ""


def choose_row_zip(row):
    if "目的地邮编" in row.index and not is_blank(row.get("目的地邮编")):
        z, fix, valid, reason = normalize_zip_value(row.get("目的地邮编"))
        return z, "源数据邮编字段", fix, valid, reason
    raw_from_destination = extract_zip_from_destination(row.get("修正后目的地", row.get("目的地", "")))
    z, fix, valid, reason = normalize_zip_value(raw_from_destination)
    if valid:
        return z, "目的地字符串提取", fix, valid, reason
    return "", "无法识别", "邮编缺失", False, "源数据无邮编字段且目的地未提取到邮编"


def normalize_transport_type(value):
    if is_blank(value):
        return "未知"
    text = str(value).strip().upper()
    if text in ["1", "1.0", "FTL"] or "整车" in text:
        return "FTL"
    if text in ["2", "2.0", "LTL"] or "散" in text or "拼" in text:
        return "LTL"
    return "未知"


def normalize_vehicle_type(value, transport_type):
    if transport_type != "FTL":
        return "不适用", "LTL不看车型", "无需审核"
    if is_blank(value):
        return "53尺大车", "车型缺失-默认大车", "默认大车"
    text = str(value)
    if "26" in text:
        return "26尺小车", "无需修正", "正常"
    if "53" in text:
        return "53尺大车", "无需修正", "正常"
    return "53尺大车", "车型无法识别-默认大车", "待确认"


def normalize_loading_type(value, transport_type):
    if transport_type == "LTL":
        return "散板", "LTL默认散板"
    if is_blank(value):
        return "未知装车类型", "装车类型缺失"
    text = str(value)
    if "地板" in text or "地" == text.strip():
        return "地板", "无需修正"
    if "卡板" in text or "托" in text or "板" in text:
        return "卡板", "无需修正"
    return "未知装车类型", "装车类型无法识别"


def build_standard_delivery_method(transport_type, vehicle_type, loading_type):
    if transport_type == "LTL":
        return "散板出库"
    if transport_type == "FTL":
        if loading_type in ["卡板", "地板"] and vehicle_type in ["26尺小车", "53尺大车"]:
            return f"{vehicle_type}-{loading_type}"
        if vehicle_type in ["26尺小车", "53尺大车"]:
            return f"{vehicle_type}-未知装车类型"
        return "FTL信息不完整"
    return "未知运输类型"


def identify_delivery_line(row):
    warehouse = str(row.get("仓库", ""))
    outbound_type = str(row.get("出库类型", ""))
    transfer_to = str(row.get("调入仓库", ""))
    state = str(row.get("目的州", "")).upper().strip()
    zip_code = str(row.get("标准邮编", "")).zfill(5) if not is_blank(row.get("标准邮编", "")) else ""
    destination = str(row.get("修正后目的地", ""))

    if outbound_type == "调拨":
        for key, line in TRANSFER_WAREHOUSE_TO_LINE.items():
            if key in transfer_to:
                return line, "调入仓库映射"

    if warehouse != "LA":
        return "非LA干线", "非LA仓暂不识别干线"

    if state == "NJ" or zip_code.startswith(("07", "08")) or "NJ" in destination.upper():
        return "LA-NJ", "州/邮编/目的地识别"
    if state in ["TX", "OK", "AR", "LA"] or zip_code.startswith(("75", "76", "77")):
        return "LA-DAL", "州/邮编识别"
    if state in ["GA", "SC", "FL", "NC", "TN", "AL"] or zip_code.startswith(("29", "30", "31", "32", "33", "34", "35", "36", "37")):
        return "LA-SAV", "州/邮编识别"
    if state in ["IL", "IN", "WI", "MI", "OH"] or zip_code.startswith(("46", "47", "48", "49", "53", "54", "55", "60", "61", "62")):
        return "LA-CHI", "州/邮编识别"

    return "未知线路", "无法识别"


# =========================
# 6. 汇总构造函数
# =========================

def build_volume_one_row_summary(df, include_us_customer=True):
    group_cols = ["仓库", "统计周期"]
    base_cols = group_cols + ["总柜量", "联宇柜量", "非联宇柜量"]
    if include_us_customer:
        base_cols += ["美国本土客户柜量"]

    ratio_cols = ["联宇柜量占比", "非联宇柜量占比"]
    if include_us_customer:
        ratio_cols += ["美国本土客户柜量占比"]
    ratio_cols += ["客户柜量比"]

    channel_cols = [
        "T1柜量", "T2柜量", "T3柜量",
        "T1柜量占比", "T2柜量占比", "T3柜量占比", "T渠道柜量比"
    ]

    output_cols = base_cols + ratio_cols + channel_cols
    if df.empty:
        return pd.DataFrame(columns=output_cols)

    rows = []
    for keys, group in df.groupby(group_cols, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = dict(zip(group_cols, keys))
        total = int(len(group))
        row["总柜量"] = total

        ly = int((group["客户类型"] == "联宇").sum())
        non_ly = int((group["客户类型"] == "非联宇").sum())
        us = int((group["客户类型"] == "美国本土客户").sum()) if include_us_customer else 0

        row["联宇柜量"] = ly
        row["非联宇柜量"] = non_ly
        if include_us_customer:
            row["美国本土客户柜量"] = us
            customer_rates = normalized_ratio_values([ly, non_ly, us])
            row["联宇柜量占比"] = customer_rates[0]
            row["非联宇柜量占比"] = customer_rates[1]
            row["美国本土客户柜量占比"] = customer_rates[2]
            row["客户柜量比"] = format_ratio_values(customer_rates)
        else:
            customer_rates = normalized_ratio_values([ly, non_ly])
            row["联宇柜量占比"] = customer_rates[0]
            row["非联宇柜量占比"] = customer_rates[1]
            row["客户柜量比"] = format_ratio_values(customer_rates)

        t1 = int((group["T渠道类型"] == "T1").sum())
        t2 = int((group["T渠道类型"] == "T2").sum())
        t3 = int((group["T渠道类型"] == "T3").sum())

        row["T1柜量"] = t1
        row["T2柜量"] = t2
        row["T3柜量"] = t3

        t_rates = normalized_ratio_values([t1, t2, t3])
        row["T1柜量占比"] = t_rates[0]
        row["T2柜量占比"] = t_rates[1]
        row["T3柜量占比"] = t_rates[2]
        row["T渠道柜量比"] = format_ratio_values(t_rates)

        rows.append(row)

    return pd.DataFrame(rows, columns=output_cols).sort_values(group_cols).reset_index(drop=True)


def build_time_ops_one_row_summary(df, duration_col, duration_label):
    result_df = build_volume_one_row_summary(df, include_us_customer=False)

    duration_cols = [
        f"总平均{duration_label}时效", f"总P80{duration_label}时效",
        f"T1平均{duration_label}时效", f"T1P80{duration_label}时效",
        f"T2平均{duration_label}时效", f"T2P80{duration_label}时效",
        f"T3平均{duration_label}时效", f"T3P80{duration_label}时效",
    ]

    if result_df.empty:
        for col in duration_cols:
            result_df[col] = np.nan
        return result_df

    duration_rows = []
    for keys, group in df.groupby(["仓库", "统计周期"], dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = {"仓库": keys[0], "统计周期": keys[1]}
        row[f"总平均{duration_label}时效"] = group[duration_col].mean()
        row[f"总P80{duration_label}时效"] = safe_p80(group[duration_col])
        for channel in ["T1", "T2", "T3"]:
            channel_series = group.loc[group["T渠道类型"] == channel, duration_col]
            row[f"{channel}平均{duration_label}时效"] = channel_series.mean()
            row[f"{channel}P80{duration_label}时效"] = safe_p80(channel_series)
        duration_rows.append(row)

    duration_df = pd.DataFrame(duration_rows)
    return result_df.merge(duration_df, on=["仓库", "统计周期"], how="left")


def mark_duration_abnormal(df, duration_col, start_col, end_col, min_days, max_days):
    df = df.copy()
    df["是否有效"] = (
        df[start_col].notna()
        & df[end_col].notna()
        & df[duration_col].notna()
        & (df[duration_col] > min_days)
        & (df[duration_col] <= max_days)
    )
    df["异常原因"] = ""
    df.loc[df[start_col].isna(), "异常原因"] = "缺少开始时间"
    df.loc[df[end_col].isna(), "异常原因"] = "缺少结束时间"
    valid_time_mask = df[start_col].notna() & df[end_col].notna() & df[duration_col].notna()
    df.loc[valid_time_mask & (df[duration_col] <= min_days), "异常原因"] = f"时效小于等于{min_days}天"
    df.loc[valid_time_mask & (df[duration_col] > max_days), "异常原因"] = f"时效大于{max_days}天"
    return df


def build_duration_summary(df, group_cols, duration_col, total_name, valid_name, include_p90=True):
    base_cols = group_cols + [total_name, valid_name, "异常数据量", "平均时效", "P80时效"]
    if include_p90:
        base_cols += ["P90时效"]
    base_cols += ["最小时效", "最大时效", "有效数据占比"]

    if df.empty:
        return pd.DataFrame(columns=base_cols)

    agg_dict = {
        total_name: ("原始行号", "count"),
        valid_name: ("是否有效", "sum"),
        "平均时效": (duration_col, "mean"),
        "P80时效": (duration_col, safe_p80),
        "最小时效": (duration_col, "min"),
        "最大时效": (duration_col, "max"),
    }
    if include_p90:
        agg_dict["P90时效"] = (duration_col, safe_p90)

    result_df = df.groupby(group_cols, dropna=False).agg(**agg_dict).reset_index()
    result_df["异常数据量"] = result_df[total_name] - result_df[valid_name]
    result_df["有效数据占比"] = result_df.apply(lambda row: safe_divide(row[valid_name], row[total_name]), axis=1)
    ordered_cols = [c for c in base_cols if c in result_df.columns]
    return result_df[ordered_cols]


# =========================
# 7. 货量 / 提柜 / 拆柜 / 旧派送
# =========================

def process_volume_analysis(df, warehouse, product_type, period_type, start_date=None, end_date=None):
    df = prepare_base_df(df)
    df = filter_warehouse(df, warehouse)
    require_columns(df, ["柜号", "ETA", "客户名称"], "货量分析")
    check_product_channel_available(df, "货量分析")

    df = filter_date_range(df, "ETA", start_date, end_date)
    df = filter_valid_container_rows(df, "货量分析")
    df = deduplicate_by_container_no(df, sort_col="ETA")
    df = add_period_column(df, period_type, "ETA")

    df["客户类型"] = df.apply(classify_customer_type_for_product_volume, axis=1)
    df["T渠道类型"] = df["产品渠道"].apply(classify_t_channel)

    detail_df = df.copy()
    result_df = build_volume_one_row_summary(detail_df, include_us_customer=True)
    return detail_df, result_df


def process_pickup_timing(df, warehouse, product_type, period_type, start_date=None, end_date=None):
    df = prepare_base_df(df)
    df = filter_warehouse(df, warehouse)
    df["客户类型"] = df.apply(classify_customer_type_for_time_ops, axis=1)

    require_columns(df, ["柜号", "提柜时间", "实际抵仓时间"], "提柜分析")
    check_product_channel_available(df, "提柜分析")
    df = filter_date_range(df, "实际抵仓时间", start_date, end_date)
    df = filter_valid_container_rows(df, "提柜分析")

    df["提柜时间"] = pd.to_datetime(df["提柜时间"], errors="coerce")
    df["实际抵仓时间"] = pd.to_datetime(df["实际抵仓时间"], errors="coerce")
    if "Available时间" in df.columns:
        df["Available时间"] = pd.to_datetime(df["Available时间"], errors="coerce")
    else:
        df["Available时间"] = pd.NaT

    df = deduplicate_by_container_no(df, sort_col="实际抵仓时间")
    df = add_period_column(df, period_type, "实际抵仓时间")
    df["T渠道类型"] = df["产品渠道"].apply(classify_t_channel)
    df["开始时间"] = np.where(df["仓库"].isin(["LA", "NJ", "SAV"]), df["Available时间"], df["提柜时间"])
    df["开始时间"] = pd.to_datetime(df["开始时间"], errors="coerce")
    df["结束时间"] = df["实际抵仓时间"]
    df["提柜时效"] = (df["结束时间"] - df["开始时间"]).dt.total_seconds() / 86400
    df = mark_duration_abnormal(df, "提柜时效", "开始时间", "结束时间", min_days=0.01, max_days=20)

    detail_df = df.copy()
    detail_df.loc[~detail_df["是否有效"], "提柜时效"] = np.nan
    result_df = build_time_ops_one_row_summary(detail_df, "提柜时效", "提柜")
    return detail_df, result_df


def process_unload_timing(df, warehouse, product_type, period_type, start_date=None, end_date=None):
    df = prepare_base_df(df)
    df = filter_warehouse(df, warehouse)
    df["客户类型"] = df.apply(classify_customer_type_for_time_ops, axis=1)

    require_columns(df, ["柜号", "实际抵仓时间", "拆柜完成时间"], "拆柜分析")
    check_product_channel_available(df, "拆柜分析")
    df = filter_date_range(df, "拆柜完成时间", start_date, end_date)
    df = filter_valid_container_rows(df, "拆柜分析")

    df["实际抵仓时间"] = pd.to_datetime(df["实际抵仓时间"], errors="coerce")
    df["拆柜完成时间"] = pd.to_datetime(df["拆柜完成时间"], errors="coerce")
    df = deduplicate_by_container_no(df, sort_col="拆柜完成时间")

    df = add_period_column(df, period_type, "拆柜完成时间")
    df["T渠道类型"] = df["产品渠道"].apply(classify_t_channel)
    df["开始时间"] = df["实际抵仓时间"]
    df["结束时间"] = df["拆柜完成时间"]
    df["拆柜时效"] = (df["结束时间"] - df["开始时间"]).dt.total_seconds() / 86400
    df = mark_duration_abnormal(df, "拆柜时效", "开始时间", "结束时间", min_days=0.01, max_days=20)

    detail_df = df.copy()
    detail_df.loc[~detail_df["是否有效"], "拆柜时效"] = np.nan
    result_df = build_time_ops_one_row_summary(detail_df, "拆柜时效", "拆柜")
    return detail_df, result_df


def process_delivery_timing(df, warehouse, product_type, period_type, start_date=None, end_date=None):
    # 保留旧入口兼容；网页新版派送使用 stage1/stage2。
    stage1, _, _ = process_delivery_stage1_from_df(df, warehouse, period_type, start_date, end_date)
    stage2 = build_delivery_stage2(stage1, period_type)
    result_df = build_delivery_timing_metrics(stage2)
    return stage2, result_df


# =========================
# 8. 派送一：原数据处理
# =========================

def process_delivery_stage1_from_df(df, warehouse, period_type="按周统计", start_date=None, end_date=None, source_name=""):
    df = normalize_columns(df)
    df = df.copy()
    if "原始行号" not in df.columns:
        df.insert(0, "原始行号", range(2, len(df) + 2))
    if source_name:
        df.insert(0, "来源文件", source_name)

    if "仓库" in df.columns:
        df["仓库"] = df["仓库"].apply(standardize_warehouse)
    elif warehouse != "四仓合并":
        df["仓库"] = warehouse
    else:
        df["仓库"] = "未知仓库"

    if warehouse != "四仓合并":
        df = df[df["仓库"] == warehouse].copy()
    else:
        df = df[df["仓库"].isin(VALID_WAREHOUSES) | (df["仓库"] == "未知仓库")].copy()

    require_columns(df, ["出库时间", "签收时间", "目的地"], "派送原数据处理")

    if start_date is not None or end_date is not None:
        df = filter_date_range(df, "出库时间", start_date, end_date)

    for col in ["出库体积", "出库卡板数", "派送成本"]:
        if col not in df.columns:
            df[col] = 0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    for col in ["派送方式", "出库类型", "运输类型", "车型", "装车类型", "批次号", "车次号", "调入仓库", "备注", "转仓地址", "批次状态"]:
        if col not in df.columns:
            df[col] = ""

    df["是否进入卡车派送分析"] = df["派送方式"].astype(str).str.contains("卡车", na=False)
    df["排除原因"] = ""
    df.loc[~df["是否进入卡车派送分析"], "排除原因"] = df.loc[~df["是否进入卡车派送分析"], "派送方式"].astype(str)

    df["业务场景"] = np.where(df["出库类型"].astype(str).str.contains("调拨", na=False), "仓间调拨/专线", "普通派送")

    df = apply_transfer_destination(df)
    df["系统产品类型"] = df["修正后目的地"].apply(classify_system_product_type)
    df["FBA/FBX"] = df["系统产品类型"].apply(classify_delivery_product_group)
    df["平台名称"] = df["修正后目的地"].apply(extract_platform_name)
    df["FBA仓点代码"] = df["修正后目的地"].apply(extract_fba_code)

    zip_results = df.apply(choose_row_zip, axis=1, result_type="expand")
    zip_results.columns = ["标准邮编", "邮编来源", "邮编修正类型", "邮编是否有效", "邮编异常原因"]
    df = pd.concat([df, zip_results], axis=1)
    df["标准邮编"] = df["标准邮编"].astype(str)
    df.loc[df["标准邮编"].isin(["nan", "None", "<NA>"]), "标准邮编"] = ""
    df["邮编前三位"] = df["标准邮编"].apply(lambda x: str(x)[:3] if len(str(x)) == 5 else "")

    if "目的州" in df.columns:
        df["目的州"] = df["目的州"].astype(str).str.upper().str.strip()
    else:
        df["目的州"] = ""

    df["标准运输类型"] = df["运输类型"].apply(normalize_transport_type)
    vehicle_results = df.apply(lambda row: normalize_vehicle_type(row.get("车型"), row.get("标准运输类型")), axis=1, result_type="expand")
    vehicle_results.columns = ["车型标准值", "车型修正类型", "车型审核结果"]
    df = pd.concat([df, vehicle_results], axis=1)

    loading_results = df.apply(lambda row: normalize_loading_type(row.get("装车类型"), row.get("标准运输类型")), axis=1, result_type="expand")
    loading_results.columns = ["装车类型标准值", "装车类型修正类型"]
    df = pd.concat([df, loading_results], axis=1)

    df["标准派送方式"] = df.apply(lambda row: build_standard_delivery_method(row["标准运输类型"], row["车型标准值"], row["装车类型标准值"]), axis=1)

    df["出库时间"] = pd.to_datetime(df["出库时间"], errors="coerce")
    df["签收时间"] = pd.to_datetime(df["签收时间"], errors="coerce")
    df["派送时效"] = (df["签收时间"] - df["出库时间"]).dt.total_seconds() / 86400
    df = mark_duration_abnormal(df, "派送时效", "出库时间", "签收时间", min_days=0, max_days=30)
    df = df.rename(columns={"是否有效": "是否有效时效", "异常原因": "时效异常原因"})
    df.loc[~df["是否有效时效"], "派送时效"] = np.nan

    line_results = df.apply(identify_delivery_line, axis=1, result_type="expand")
    line_results.columns = ["专线线路", "专线识别方式"]
    df = pd.concat([df, line_results], axis=1)

    df = add_period_column(df, period_type, "出库时间")
    df["目的地邮编待补充"] = ~df["邮编是否有效"]

    # 未识别邮编的数据放在最下端，方便复制批次号人工补目的地。
    df = df.sort_values(["目的地邮编待补充", "出库时间"], ascending=[True, True]).reset_index(drop=True)

    exclude_df = df[~df["是否进入卡车派送分析"]].copy()
    zip_audit_df = df[df["目的地邮编待补充"]].copy()
    return df, exclude_df, zip_audit_df


def process_delivery_stage1_from_files(file_dfs, warehouse, period_type="按周统计", start_date=None, end_date=None):
    processed = []
    for source_name, df in file_dfs:
        stage1, _, _ = process_delivery_stage1_from_df(df, warehouse, period_type, start_date, end_date, source_name=source_name)
        processed.append(stage1)
    if not processed:
        empty = pd.DataFrame()
        return empty, empty, empty
    stage1_all = pd.concat(processed, ignore_index=True)
    stage1_all = stage1_all.sort_values(["目的地邮编待补充", "出库时间"], ascending=[True, True]).reset_index(drop=True)
    exclude_df = stage1_all[~stage1_all["是否进入卡车派送分析"]].copy()
    zip_audit_df = stage1_all[stage1_all["目的地邮编待补充"]].copy()
    return stage1_all, exclude_df, zip_audit_df


# =========================
# 9. 派送二：匹配补充 + 批次/车次聚合 + 指标
# =========================

def read_stage1_sheet(excel_file):
    excel_file.seek(0)
    xls = pd.ExcelFile(excel_file)
    sheet_name = "派送一_清洗明细" if "派送一_清洗明细" in xls.sheet_names else xls.sheet_names[0]
    return pd.read_excel(excel_file, sheet_name=sheet_name, dtype=str)


def prepare_match_file(match_df):
    match_df = normalize_columns(match_df)
    require_columns(match_df, ["批次号"], "派送数据匹配文件")
    if "目的地邮编" not in match_df.columns and "标准邮编" not in match_df.columns:
        raise ValueError("派送数据匹配文件需要包含目的地邮编/标准邮编字段。")
    if "标准邮编" not in match_df.columns:
        match_df["标准邮编"] = match_df["目的地邮编"]

    zip_norm = match_df["标准邮编"].apply(normalize_zip_value)
    match_df["补充标准邮编"] = [x[0] for x in zip_norm]
    match_df["补充邮编修正类型"] = [x[1] for x in zip_norm]
    match_df["补充邮编是否有效"] = [x[2] for x in zip_norm]
    match_df["补充邮编异常原因"] = [x[3] for x in zip_norm]

    if "目的州" not in match_df.columns:
        match_df["目的州"] = ""
    keep_cols = ["批次号", "补充标准邮编", "目的州", "补充邮编修正类型", "补充邮编是否有效", "补充邮编异常原因"]
    return match_df[keep_cols].drop_duplicates(subset=["批次号"], keep="last")


def apply_destination_match(stage1_df, match_df):
    df = normalize_columns(stage1_df)
    match = prepare_match_file(match_df)
    df["批次号"] = df["批次号"].astype(str)
    match["批次号"] = match["批次号"].astype(str)
    df = df.merge(match, on="批次号", how="left")

    need_fill = (~df["邮编是否有效"].astype(str).isin(["True", "true", "1"])) & df["补充邮编是否有效"].astype(str).isin(["True", "true", "1"])
    df.loc[need_fill, "标准邮编"] = df.loc[need_fill, "补充标准邮编"]
    df.loc[need_fill, "邮编来源"] = "批次号匹配补充"
    df.loc[need_fill, "邮编修正类型"] = df.loc[need_fill, "补充邮编修正类型"]
    df.loc[need_fill, "邮编是否有效"] = True
    df.loc[need_fill, "邮编异常原因"] = ""

    if "目的州" not in df.columns:
        df["目的州"] = ""
    state_fill = df["目的州"].apply(is_blank) & df["目的州_y"].notna() if "目的州_y" in df.columns else pd.Series(False, index=df.index)
    if "目的州_y" in df.columns:
        df.loc[state_fill, "目的州"] = df.loc[state_fill, "目的州_y"]
        df = df.drop(columns=["目的州_y"], errors="ignore")
    if "目的州_x" in df.columns:
        df = df.rename(columns={"目的州_x": "目的州"})

    df["标准邮编"] = df["标准邮编"].fillna("").astype(str).str.zfill(5)
    df.loc[df["标准邮编"].isin(["00000", "nan", "None", "<NA>"]), "标准邮编"] = ""
    df["邮编前三位"] = df["标准邮编"].apply(lambda x: str(x)[:3] if len(str(x)) == 5 else "")
    df["目的地邮编待补充"] = ~df["邮编是否有效"].astype(str).isin(["True", "true", "1"])

    line_results = df.apply(identify_delivery_line, axis=1, result_type="expand")
    line_results.columns = ["专线线路", "专线识别方式"]
    df = df.drop(columns=["专线线路", "专线识别方式"], errors="ignore")
    df = pd.concat([df, line_results], axis=1)
    return df


def first_nonblank(series):
    for v in series:
        if not is_blank(v):
            return v
    return ""


def combine_unique(series):
    values = [str(v) for v in series if not is_blank(v)]
    values = list(dict.fromkeys(values))
    return ",".join(values)


def resolve_group_loading(series):
    values = [str(v) for v in series if not is_blank(v)]
    if any("地板" in v for v in values):
        return "地板"
    if any("卡板" in v for v in values):
        return "卡板"
    if any("散板" in v for v in values):
        return "散板"
    return "未知装车类型"


def resolve_group_vehicle(series):
    values = [str(v) for v in series if not is_blank(v)]
    if any("53" in v or "大车" in v for v in values):
        return "53尺大车"
    if any("26" in v or "小车" in v for v in values):
        return "26尺小车"
    return "53尺大车"


def build_delivery_stage2(stage1_df, period_type="按周统计"):
    df = stage1_df.copy()
    for col in ["出库体积", "出库卡板数", "派送成本"]:
        if col not in df.columns:
            df[col] = 0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    for col in ["出库时间", "签收时间"]:
        df[col] = pd.to_datetime(df[col], errors="coerce")

    if "是否进入卡车派送分析" in df.columns:
        df = df[df["是否进入卡车派送分析"].astype(str).isin(["True", "true", "1", "是", "卡车派送"]) | (df["是否进入卡车派送分析"] == True)].copy()

    if "标准运输类型" not in df.columns:
        df["标准运输类型"] = df["运输类型"].apply(normalize_transport_type)

    rows = []

    ftl_df = df[df["标准运输类型"] == "FTL"].copy()
    ltl_df = df[df["标准运输类型"] != "FTL"].copy()

    if not ftl_df.empty:
        ftl_df["车次号"] = ftl_df["车次号"].astype(str).replace({"nan": ""})
        ftl_df["车次聚合键"] = np.where(ftl_df["车次号"].apply(is_blank), "FTL_NO_TRIP_" + ftl_df["原始行号"].astype(str), ftl_df["车次号"])
        for trip, group in ftl_df.groupby("车次聚合键", dropna=False):
            vehicle = resolve_group_vehicle(group.get("车型标准值", pd.Series(dtype=str)))
            loading = resolve_group_loading(group.get("装车类型标准值", pd.Series(dtype=str)))
            method = build_standard_delivery_method("FTL", vehicle, loading)
            start_time = group["出库时间"].min()
            end_time = group["签收时间"].max()
            duration = (end_time - start_time).total_seconds() / 86400 if pd.notna(start_time) and pd.notna(end_time) else np.nan
            product_types = list(dict.fromkeys([x for x in group["系统产品类型"].astype(str) if not is_blank(x)]))
            product_type = product_types[0] if len(product_types) == 1 else "混合目的地"
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
                "批次出库时间": start_time,
                "批次签收时间": end_time,
                "派送时效": duration,
                "出库体积": group["出库体积"].sum(),
                "出库卡板数": group["出库卡板数"].sum(),
                "派送成本": group["派送成本"].sum(),
                "系统产品类型": product_type,
                "FBA出库体积": group.loc[group["FBA/FBX"] == "FBA", "出库体积"].sum(),
                "FBX出库体积": group.loc[group["FBA/FBX"] == "FBX", "出库体积"].sum(),
                "平台名称": combine_unique(group["平台名称"]),
                "标准邮编集合": combine_unique(group["标准邮编"]),
                "邮编前三位集合": combine_unique(group["邮编前三位"]),
                "目的州": combine_unique(group["目的州"]),
                "专线线路": first_nonblank(group["专线线路"]),
                "专线识别方式": first_nonblank(group["专线识别方式"]),
                "是否混合目的地": len(product_types) > 1,
                "是否混装": len(set([x for x in group["装车类型标准值"].astype(str) if not is_blank(x)])) > 1,
            }
            rows.append(row)

    if not ltl_df.empty:
        for _, r in ltl_df.iterrows():
            row = {
                "分析批次ID": f"LTL_{r.get('原始行号', '')}",
                "仓库": r.get("仓库", ""),
                "标准运输类型": r.get("标准运输类型", "LTL"),
                "派送方式": "散板出库" if r.get("标准运输类型") == "LTL" else r.get("标准派送方式", "未知运输类型"),
                "车型标准值": r.get("车型标准值", "不适用"),
                "装车类型标准值": r.get("装车类型标准值", "散板"),
                "车次号": r.get("车次号", ""),
                "批次号集合": r.get("批次号", ""),
                "出库类型": r.get("出库类型", ""),
                "业务场景": r.get("业务场景", ""),
                "批次出库时间": r.get("出库时间", pd.NaT),
                "批次签收时间": r.get("签收时间", pd.NaT),
                "派送时效": r.get("派送时效", np.nan),
                "出库体积": r.get("出库体积", 0),
                "出库卡板数": r.get("出库卡板数", 0),
                "派送成本": r.get("派送成本", 0),
                "系统产品类型": r.get("系统产品类型", ""),
                "FBA出库体积": r.get("出库体积", 0) if r.get("FBA/FBX") == "FBA" else 0,
                "FBX出库体积": r.get("出库体积", 0) if r.get("FBA/FBX") == "FBX" else 0,
                "平台名称": r.get("平台名称", ""),
                "标准邮编集合": r.get("标准邮编", ""),
                "邮编前三位集合": r.get("邮编前三位", ""),
                "目的州": r.get("目的州", ""),
                "专线线路": r.get("专线线路", ""),
                "专线识别方式": r.get("专线识别方式", ""),
                "是否混合目的地": False,
                "是否混装": False,
            }
            rows.append(row)

    stage2 = pd.DataFrame(rows)
    if stage2.empty:
        return stage2

    stage2["批次出库时间"] = pd.to_datetime(stage2["批次出库时间"], errors="coerce")
    stage2["批次签收时间"] = pd.to_datetime(stage2["批次签收时间"], errors="coerce")
    stage2["是否有效时效"] = stage2["批次出库时间"].notna() & stage2["批次签收时间"].notna() & stage2["派送时效"].notna() & (stage2["派送时效"] > 0) & (stage2["派送时效"] <= 30)
    stage2.loc[~stage2["是否有效时效"], "派送时效"] = np.nan
    stage2 = add_period_column(stage2, period_type, "批次出库时间")
    return stage2


def build_fba_fbx_volume_ratio(stage2):
    cols = ["仓库", "统计周期", "FBA出库体积", "FBX出库体积", "FBA占比", "FBX占比", "FBA/FBX货量比"]
    if stage2.empty:
        return pd.DataFrame(columns=cols)
    rows = []
    for keys, group in stage2.groupby(["仓库", "统计周期"], dropna=False):
        fba = group["FBA出库体积"].sum()
        fbx = group["FBX出库体积"].sum()
        rates = normalized_ratio_values([fba, fbx])
        rows.append({
            "仓库": keys[0], "统计周期": keys[1],
            "FBA出库体积": fba, "FBX出库体积": fbx,
            "FBA占比": rates[0], "FBX占比": rates[1],
            "FBA/FBX货量比": format_ratio_values(rates)
        })
    return pd.DataFrame(rows, columns=cols)


def build_dispatch_summary(stage2):
    cols = ["仓库", "统计周期", "总发车", "区域发车", "出库体积", "派送成本", "FTL发车", "LTL发车"]
    if stage2.empty:
        return pd.DataFrame(columns=cols)
    result = stage2.groupby(["仓库", "统计周期"], dropna=False).agg(
        总发车=("分析批次ID", "count"),
        区域发车=("专线线路", lambda x: (x.astype(str) != "未知线路").sum()),
        出库体积=("出库体积", "sum"),
        派送成本=("派送成本", "sum"),
        FTL发车=("标准运输类型", lambda x: (x == "FTL").sum()),
        LTL发车=("标准运输类型", lambda x: (x == "LTL").sum()),
    ).reset_index()
    return result[cols]


def build_linehaul_summary(stage2):
    cols = ["仓库", "统计周期", "专线线路", "发车数", "出库体积", "派送成本", "平均派送时效", "P80派送时效"]
    if stage2.empty:
        return pd.DataFrame(columns=cols)
    df = stage2[~stage2["专线线路"].isin(["", "未知线路", "非LA干线"])].copy()
    if df.empty:
        return pd.DataFrame(columns=cols)
    result = df.groupby(["仓库", "统计周期", "专线线路"], dropna=False).agg(
        发车数=("分析批次ID", "count"),
        出库体积=("出库体积", "sum"),
        派送成本=("派送成本", "sum"),
        平均派送时效=("派送时效", "mean"),
        P80派送时效=("派送时效", safe_p80),
    ).reset_index()
    return result[cols]


def build_delivery_timing_metrics(stage2):
    if stage2.empty:
        return pd.DataFrame(columns=["仓库", "统计周期", "派送方式", "有效批次数", "平均派送时效", "P80派送时效"])
    result = stage2.groupby(["仓库", "统计周期", "派送方式"], dropna=False).agg(
        总批次数=("分析批次ID", "count"),
        有效批次数=("是否有效时效", "sum"),
        平均派送时效=("派送时效", "mean"),
        P80派送时效=("派送时效", safe_p80),
    ).reset_index()
    return result


def build_platform_volume(stage2):
    cols = ["仓库", "统计周期", "平台名称", "平台仓出库体积", "平台仓占比"]
    if stage2.empty:
        return pd.DataFrame(columns=cols)
    df = stage2[stage2["平台名称"].astype(str).ne("非平台/未知") & stage2["平台名称"].notna()].copy()
    if df.empty:
        return pd.DataFrame(columns=cols)
    total = df.groupby(["仓库", "统计周期"], dropna=False)["FBX出库体积"].sum().rename("FBX平台总量").reset_index()
    result = df.groupby(["仓库", "统计周期", "平台名称"], dropna=False).agg(平台仓出库体积=("FBX出库体积", "sum")).reset_index()
    result = result.merge(total, on=["仓库", "统计周期"], how="left")
    result["平台仓占比"] = result.apply(lambda r: safe_divide(r["平台仓出库体积"], r["FBX平台总量"]), axis=1)
    return result[cols]


def process_delivery_stage2_with_match(stage1_df, match_df, period_type="按周统计"):
    matched_detail = apply_destination_match(stage1_df, match_df)
    stage2 = build_delivery_stage2(matched_detail, period_type)
    metrics = {
        "派送一_匹配后明细": matched_detail,
        "派送二_批次车次聚合": stage2,
        "FBA_FBX货量比": build_fba_fbx_volume_ratio(stage2),
        "发车汇总": build_dispatch_summary(stage2),
        "干线发车货量": build_linehaul_summary(stage2),
        "派送时效": build_delivery_timing_metrics(stage2),
        "平台仓货量": build_platform_volume(stage2),
        "邮编异常审核": matched_detail[matched_detail["目的地邮编待补充"]].copy(),
    }
    for key, value in metrics.items():
        if isinstance(value, pd.DataFrame):
            metrics[key] = round_output_numbers(value, RESULT_DECIMALS)
    return metrics


# =========================
# 10. 总入口函数：给 app.py 调用
# =========================

def process_uploaded_file(
    uploaded_file,
    sheet_name,
    warehouse,
    product_type,
    analysis_module,
    period_type,
    start_date=None,
    end_date=None
):
    uploaded_file.seek(0)
    df = pd.read_excel(uploaded_file, sheet_name=sheet_name)

    if analysis_module == "货量分析":
        detail_df, result_df = process_volume_analysis(df, warehouse, product_type, period_type, start_date, end_date)
    elif analysis_module in ["提柜分析", "提柜时效分析"]:
        detail_df, result_df = process_pickup_timing(df, warehouse, product_type, period_type, start_date, end_date)
    elif analysis_module in ["拆柜分析", "拆柜时效分析"]:
        detail_df, result_df = process_unload_timing(df, warehouse, product_type, period_type, start_date, end_date)
    elif analysis_module in ["派送分析", "派送时效分析"]:
        detail_df, result_df = process_delivery_timing(df, warehouse, product_type, period_type, start_date, end_date)
    else:
        raise ValueError(f"暂不支持该分析模块：{analysis_module}")

    result_df = round_output_numbers(result_df, RESULT_DECIMALS)
    return detail_df, result_df, analysis_module

import re
import numpy as np
import pandas as pd


# =========================
# 1. 基础配置
# =========================

WAREHOUSE_MAP = {
    "美西二号仓": "LA", "美西仓": "LA", "洛杉矶": "LA", "LA": "LA", "LAX": "LA",
    "达拉斯盈仓": "DAL", "达拉斯": "DAL", "DAL": "DAL", "Dallas": "DAL",
    "萨凡纳盈仓": "SAV", "萨凡纳": "SAV", "SAV": "SAV", "Savannah": "SAV",
    "新泽西二号仓": "NJ", "新泽西": "NJ", "NJ": "NJ", "New Jersey": "NJ",
}

FIELD_ALIASES = {
    "仓库": ["仓库", "仓点", "仓库名称", "所属仓", "目的仓", "Warehouse"],
    "客户名称": ["客户名称", "客户", "客户名", "客户公司", "Customer", "Customer Name"],
    "产品渠道": ["产品渠道", "渠道", "T渠道", "服务渠道", "产品通道"],

    "出库时间": ["出库时间", "实际出库时间", "发车时间", "Outbound Time", "Ship Time"],
    "签收时间": ["签收时间", "实际签收时间", "POD时间", "妥投时间", "Delivered Time", "Delivery Time"],
    "目的地": ["目的地", "目的地址", "派送地址", "收货地址", "Destination"],
    "转仓地址": ["转仓地址", "中转地址", "转运地址", "Transfer Address"],
    "派送成本": ["派送成本", "成本", "Delivery Cost"],
    "车次号": ["车次号", "车次", "批次号", "批次", "Load No", "Trip No"],

    "提柜时间": ["提柜时间", "实际提柜时间", "提柜日期", "Pickup Time", "Pick Up Time"],
    "Available时间": ["Available时间", "AVAILABLE时间", "Available Time", "可提时间", "码头可提时间"],
    "实际抵仓时间": ["实际抵仓时间", "实际到仓时间", "抵仓时间", "到仓时间", "Actual Arrival Time", "Arrival Time"],
    "拆柜完成时间": ["拆柜完成时间", "拆柜结束时间", "拆柜完毕时间", "拆柜完成日期", "Unload Finish Time"],

    "体积": ["体积", "方数", "CBM", "Volume", "出库体积"],
    "卡板数": ["卡板数", "板数", "托盘数", "Pallets", "出库卡板数"],
    "工作单号": ["工作单号", "工作单", "运单号", "订单号", "SO", "SO号"],
    "柜号": ["柜号", "箱号", "Container", "Container No", "ContainerNo", "Container Number"],

    "平台仓点": ["平台仓点", "平台仓", "平台仓库", "仓点名称"],
    "平台名称": ["平台名称", "平台", "平台类型"],
    "货型": ["货型", "货物类型", "装车类型", "货物形态"],
}

PLATFORM_KEYWORDS = [
    "Walmart", "WalMart", "TiKToK", "TikTok", "SHEIN", "希音", "谷仓", "Wayfair", "万邑通", "运去哪", "乐歌", "盈仓"
]

EXCLUDE_FBX_KEYWORDS = [
    "Amazon", "AMAZON", "amazon", "商业地址", "私人地址", "住宅地址", "首页地址私人地址", "首页地址商业地址"
]

LOW_VOLUME_TICKET_THRESHOLD = 2
LOW_VOLUME_SHARE_THRESHOLD = 0.005
CONTAINER_NO_PATTERN = re.compile(r"^[A-Z]{3}[UJZ]\d{7}$")


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
            return df[df["仓库"].isin(["LA", "NJ", "SAV", "DAL"])].copy()
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


# =========================
# 3. 客户类型 / T渠道逻辑
# =========================

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

def apply_transfer_destination(df):
    df = df.copy()
    if "目的地" not in df.columns:
        raise ValueError("缺少目的地字段")

    df["原始目的地"] = df["目的地"]
    if "转仓地址" in df.columns:
        transfer_text = df["转仓地址"].astype(str).str.strip()
        valid_transfer = (
            df["转仓地址"].notna()
            & (~transfer_text.isin(["", "nan", "NaN", "None", "none", "null", "NULL", "-"]))
            & (~transfer_text.str.fullmatch(r"\d+"))
        )
        df.loc[valid_transfer, "目的地"] = df.loc[valid_transfer, "转仓地址"]
    df["修正后目的地"] = df["目的地"]
    return df


def classify_system_product_type(destination):
    destination = "" if pd.isna(destination) else str(destination)
    if re.search(r"Amazon", destination, flags=re.IGNORECASE):
        return "FBA"
    if any(keyword in destination for keyword in PLATFORM_KEYWORDS):
        return "FBX平台仓"
    if any(keyword in destination for keyword in ["商业地址", "私人地址", "住宅地址"]):
        return "FBX非平台地址"
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


# =========================
# 5. 平台 / FBA仓点识别逻辑
# =========================

def extract_platform_name(text):
    text = "" if pd.isna(text) else str(text)
    for keyword in PLATFORM_KEYWORDS:
        if keyword in text:
            if keyword in ["TiKToK", "TikTok"]:
                return "TiKToK"
            if keyword in ["SHEIN", "希音"]:
                return "希音"
            if keyword in ["WalMart", "Walmart"]:
                return "Walmart"
            return keyword
    return "未知平台"


def extract_fba_code(text):
    text = "" if pd.isna(text) else str(text).strip()
    match = re.search(r"Amazon[-_\s]*([A-Z0-9]+)", text, flags=re.IGNORECASE)
    if match:
        return "Amazon-" + match.group(1).upper()
    return text


def add_customer_mix_columns(result_df, detail_df, group_cols):
    if detail_df.empty or "客户类型" not in detail_df.columns:
        result_df["联宇票数"] = 0
        result_df["非联宇票数"] = 0
        result_df["美国本土客户票数"] = 0
        return result_df

    customer_mix = detail_df.groupby(group_cols + ["客户类型"], dropna=False).size().unstack(fill_value=0).reset_index()
    for col in ["联宇", "非联宇", "美国本土客户"]:
        if col not in customer_mix.columns:
            customer_mix[col] = 0
    customer_mix = customer_mix.rename(columns={"联宇": "联宇票数", "非联宇": "非联宇票数", "美国本土客户": "美国本土客户票数"})
    keep_cols = group_cols + ["联宇票数", "非联宇票数", "美国本土客户票数"]
    result_df = result_df.merge(customer_mix[keep_cols], on=group_cols, how="left")
    for col in ["联宇票数", "非联宇票数", "美国本土客户票数"]:
        result_df[col] = result_df[col].fillna(0).astype(int)
    return result_df


def add_rank_and_share(result_df, sort_col="总体积"):
    result_df = result_df.copy()
    if result_df.empty:
        return result_df

    total_volume = result_df["总体积"].sum() if "总体积" in result_df.columns else 0
    total_pallets = result_df["总卡板数"].sum() if "总卡板数" in result_df.columns else 0
    total_tickets = result_df["票数"].sum() if "票数" in result_df.columns else 0
    total_cost = result_df["总派送成本"].sum() if "总派送成本" in result_df.columns else 0

    result_df["体积占比"] = result_df["总体积"].apply(lambda x: safe_divide(x, total_volume))
    result_df["卡板数占比"] = result_df["总卡板数"].apply(lambda x: safe_divide(x, total_pallets))
    result_df["票数占比"] = result_df["票数"].apply(lambda x: safe_divide(x, total_tickets))
    result_df["成本占比"] = result_df["总派送成本"].apply(lambda x: safe_divide(x, total_cost))
    result_df = result_df.sort_values(["仓库", "统计周期", sort_col], ascending=[True, True, False])
    result_df["货量排行"] = result_df.groupby(["仓库", "统计周期"])[sort_col].rank(method="first", ascending=False).astype(int)
    return result_df


# =========================
# 6. FBA 仓点货量排行
# =========================

def process_fba_warehouse_rank(df, warehouse, product_type, period_type, start_date=None, end_date=None):
    df = prepare_base_df(df)
    df = filter_warehouse(df, warehouse)
    df["客户类型"] = df.apply(classify_customer_type_for_volume, axis=1)

    require_columns(df, ["出库时间", "目的地"], "FBA仓点货量排行")
    df = filter_date_range(df, "出库时间", start_date, end_date)
    df = add_period_column(df, period_type, "出库时间")
    df = apply_transfer_destination(df)
    df["系统产品类型"] = df["修正后目的地"].apply(classify_system_product_type)

    destination = df["修正后目的地"].astype(str)
    detail_df = df[destination.str.contains("Amazon", na=False, case=False, regex=True)].copy()
    detail_df["FBA仓点"] = detail_df["修正后目的地"].apply(extract_fba_code)
    detail_df = ensure_numeric_cols(detail_df, ["体积", "卡板数", "派送成本"])

    group_cols = ["仓库", "统计周期", "FBA仓点"]
    result_df = detail_df.groupby(group_cols, dropna=False).agg(
        票数=("原始行号", "count"),
        总体积=("体积", "sum"),
        总卡板数=("卡板数", "sum"),
        总派送成本=("派送成本", "sum"),
    ).reset_index()

    if "车次号" in detail_df.columns and not detail_df.empty:
        dispatch_df = detail_df.dropna(subset=["车次号"]).groupby(group_cols, dropna=False)["车次号"].nunique().rename("发车量").reset_index()
        result_df = result_df.merge(dispatch_df, on=group_cols, how="left")
    else:
        result_df["发车量"] = np.nan

    result_df = add_customer_mix_columns(result_df, detail_df, group_cols)
    result_df = add_rank_and_share(result_df, sort_col="总体积")
    return detail_df, result_df


# =========================
# 7. FBX 平台仓点货量分析
# =========================

def process_fbx_platform_volume(df, warehouse, product_type, period_type, start_date=None, end_date=None):
    df = prepare_base_df(df)
    df = filter_warehouse(df, warehouse)
    df["客户类型"] = df.apply(classify_customer_type_for_volume, axis=1)

    require_columns(df, ["出库时间", "目的地"], "FBX平台仓点货量分析")
    df = filter_date_range(df, "出库时间", start_date, end_date)
    df = add_period_column(df, period_type, "出库时间")
    df = apply_transfer_destination(df)
    df["系统产品类型"] = df["修正后目的地"].apply(classify_system_product_type)

    destination = df["修正后目的地"].astype(str)
    is_excluded = contains_any(destination, EXCLUDE_FBX_KEYWORDS)
    is_platform = contains_any(destination, PLATFORM_KEYWORDS)
    detail_df = df[(~is_excluded) & is_platform].copy()

    detail_df["平台名称"] = detail_df["修正后目的地"].apply(extract_platform_name)
    detail_df["平台仓点"] = detail_df["修正后目的地"].astype(str).str.strip()
    detail_df = ensure_numeric_cols(detail_df, ["体积", "卡板数", "派送成本"])

    group_cols = ["仓库", "统计周期", "平台名称", "平台仓点"]
    result_df = detail_df.groupby(group_cols, dropna=False).agg(
        票数=("原始行号", "count"),
        总体积=("体积", "sum"),
        总卡板数=("卡板数", "sum"),
        总派送成本=("派送成本", "sum"),
    ).reset_index()

    if "车次号" in detail_df.columns and not detail_df.empty:
        dispatch_df = detail_df.dropna(subset=["车次号"]).groupby(group_cols, dropna=False)["车次号"].nunique().rename("发车量").reset_index()
        result_df = result_df.merge(dispatch_df, on=group_cols, how="left")
    else:
        result_df["发车量"] = np.nan

    result_df = add_customer_mix_columns(result_df, detail_df, group_cols)
    result_df = add_rank_and_share(result_df, sort_col="总体积")

    if not result_df.empty:
        result_df["剔除建议"] = "保留观察"
        low_ticket = result_df["票数"] <= LOW_VOLUME_TICKET_THRESHOLD
        low_share = result_df["体积占比"] < LOW_VOLUME_SHARE_THRESHOLD
        unknown_name = result_df["平台仓点"].astype(str).str.contains("null|None|nan", case=False, na=False)
        result_df.loc[low_ticket | low_share | unknown_name, "剔除建议"] = "低货量-待评估"
    return detail_df, result_df


# =========================
# 8. 时效分析通用函数
# =========================

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


def build_duration_summary(df, group_cols, duration_col, total_name, valid_name):
    if df.empty:
        return pd.DataFrame(columns=group_cols + [
            total_name, valid_name, "异常数据量", "平均时效", "P80时效", "P90时效", "最小时效", "最大时效", "有效数据占比"
        ])

    result_df = df.groupby(group_cols, dropna=False).agg(
        **{
            total_name: ("原始行号", "count"),
            valid_name: ("是否有效", "sum"),
            "平均时效": (duration_col, "mean"),
            "P80时效": (duration_col, lambda x: x.dropna().quantile(0.8) if x.dropna().shape[0] else np.nan),
            "P90时效": (duration_col, lambda x: x.dropna().quantile(0.9) if x.dropna().shape[0] else np.nan),
            "最小时效": (duration_col, "min"),
            "最大时效": (duration_col, "max"),
        }
    ).reset_index()
    result_df["异常数据量"] = result_df[total_name] - result_df[valid_name]
    result_df["有效数据占比"] = result_df.apply(lambda row: safe_divide(row[valid_name], row[total_name]), axis=1)
    return result_df


def build_time_ops_one_row_summary(df, duration_col, duration_label):
    """
    提柜 / 拆柜结果专用：一行呈现柜量结构 + 时效结构。
    每行粒度为：仓库 + 统计周期。
    """
    group_cols = ["仓库", "统计周期"]
    output_cols = group_cols + [
        "总柜量", "联宇柜量", "非联宇柜量", "T1柜量", "T2柜量", "T3柜量",
        f"总平均{duration_label}时效", f"T1平均{duration_label}时效", f"T2平均{duration_label}时效", f"T3平均{duration_label}时效",
    ]

    if df.empty:
        return pd.DataFrame(columns=output_cols)

    rows = []
    for keys, group in df.groupby(group_cols, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = dict(zip(group_cols, keys))
        row["总柜量"] = int(len(group))
        row["联宇柜量"] = int((group["客户类型"] == "联宇").sum())
        row["非联宇柜量"] = int((group["客户类型"] == "非联宇").sum())
        row["T1柜量"] = int((group["T渠道类型"] == "T1").sum())
        row["T2柜量"] = int((group["T渠道类型"] == "T2").sum())
        row["T3柜量"] = int((group["T渠道类型"] == "T3").sum())
        row[f"总平均{duration_label}时效"] = group[duration_col].mean()
        row[f"T1平均{duration_label}时效"] = group.loc[group["T渠道类型"] == "T1", duration_col].mean()
        row[f"T2平均{duration_label}时效"] = group.loc[group["T渠道类型"] == "T2", duration_col].mean()
        row[f"T3平均{duration_label}时效"] = group.loc[group["T渠道类型"] == "T3", duration_col].mean()
        rows.append(row)

    return pd.DataFrame(rows, columns=output_cols).sort_values(group_cols).reset_index(drop=True)


# =========================
# 9. 派送时效分析
# =========================

def process_delivery_timing(df, warehouse, product_type, period_type, start_date=None, end_date=None):
    df = prepare_base_df(df)
    df = filter_warehouse(df, warehouse)
    df["客户类型"] = df.apply(classify_customer_type_for_volume, axis=1)
    require_columns(df, ["出库时间", "签收时间"], "派送时效分析")
    df = filter_date_range(df, "出库时间", start_date, end_date)

    if "目的地" in df.columns:
        df = apply_transfer_destination(df)
        df["系统产品类型"] = df["修正后目的地"].apply(classify_system_product_type)
        df = filter_by_product_type(df, product_type)
    else:
        df["系统产品类型"] = "未知"

    df = add_period_column(df, period_type, "出库时间")
    df["出库时间"] = pd.to_datetime(df["出库时间"], errors="coerce")
    df["签收时间"] = pd.to_datetime(df["签收时间"], errors="coerce")
    df["派送时效"] = (df["签收时间"] - df["出库时间"]).dt.total_seconds() / 86400
    df = mark_duration_abnormal(df, "派送时效", "出库时间", "签收时间", min_days=0.01, max_days=30)

    detail_df = df.copy()
    detail_df.loc[~detail_df["是否有效"], "派送时效"] = np.nan
    group_cols = ["仓库", "统计周期", "系统产品类型", "客户类型", "T渠道类型"]
    result_df = build_duration_summary(detail_df, group_cols, "派送时效", total_name="总票数", valid_name="有效票数")
    result_df = result_df.rename(columns={
        "平均时效": "平均派送时效", "P80时效": "P80派送时效", "P90时效": "P90派送时效",
        "最小时效": "最小派送时效", "最大时效": "最大派送时效",
    })
    return detail_df, result_df


# =========================
# 10. 提柜时效分析
# =========================

def process_pickup_timing(df, warehouse, product_type, period_type, start_date=None, end_date=None):
    df = prepare_base_df(df)
    df = filter_warehouse(df, warehouse)
    df["客户类型"] = df.apply(classify_customer_type_for_time_ops, axis=1)

    require_columns(df, ["柜号", "提柜时间", "实际抵仓时间"], "提柜时效分析")
    check_product_channel_available(df, "提柜时效分析")
    df = filter_date_range(df, "实际抵仓时间", start_date, end_date)
    df = filter_valid_container_rows(df, "提柜时效分析")

    df["提柜时间"] = pd.to_datetime(df["提柜时间"], errors="coerce")
    df["实际抵仓时间"] = pd.to_datetime(df["实际抵仓时间"], errors="coerce")
    if "Available时间" in df.columns:
        df["Available时间"] = pd.to_datetime(df["Available时间"], errors="coerce")
    else:
        df["Available时间"] = pd.NaT

    if "工作单号" in df.columns:
        df = df.sort_values("提柜时间").drop_duplicates("工作单号", keep="last")

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


# =========================
# 11. 拆柜时效分析
# =========================

def process_unload_timing(df, warehouse, product_type, period_type, start_date=None, end_date=None):
    df = prepare_base_df(df)
    df = filter_warehouse(df, warehouse)
    df["客户类型"] = df.apply(classify_customer_type_for_time_ops, axis=1)

    require_columns(df, ["柜号", "实际抵仓时间", "拆柜完成时间"], "拆柜时效分析")
    check_product_channel_available(df, "拆柜时效分析")
    df = filter_date_range(df, "拆柜完成时间", start_date, end_date)
    df = filter_valid_container_rows(df, "拆柜时效分析")

    df["实际抵仓时间"] = pd.to_datetime(df["实际抵仓时间"], errors="coerce")
    df["拆柜完成时间"] = pd.to_datetime(df["拆柜完成时间"], errors="coerce")
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


# =========================
# 12. 总入口函数：给 app.py 调用
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

    if analysis_module == "FBA仓点货量排行":
        detail_df, result_df = process_fba_warehouse_rank(df, warehouse, product_type, period_type, start_date, end_date)
    elif analysis_module == "FBX平台仓点货量分析":
        detail_df, result_df = process_fbx_platform_volume(df, warehouse, product_type, period_type, start_date, end_date)
    elif analysis_module == "派送时效分析":
        detail_df, result_df = process_delivery_timing(df, warehouse, product_type, period_type, start_date, end_date)
    elif analysis_module == "提柜时效分析":
        detail_df, result_df = process_pickup_timing(df, warehouse, product_type, period_type, start_date, end_date)
    elif analysis_module == "拆柜时效分析":
        detail_df, result_df = process_unload_timing(df, warehouse, product_type, period_type, start_date, end_date)
    else:
        raise ValueError(f"暂不支持该分析模块：{analysis_module}")

    for col in result_df.select_dtypes(include=["float", "float64"]).columns:
        result_df[col] = result_df[col].round(4)

    return detail_df, result_df, analysis_module

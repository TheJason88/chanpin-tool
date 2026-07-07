import re
import numpy as np
import pandas as pd


WAREHOUSE_MAP = {
    "美西二号仓": "LA",
    "美西仓": "LA",
    "洛杉矶": "LA",
    "LA": "LA",
    "LAX": "LA",

    "达拉斯盈仓": "DAL",
    "达拉斯": "DAL",
    "DAL": "DAL",
    "Dallas": "DAL",

    "萨凡纳盈仓": "SAV",
    "萨凡纳": "SAV",
    "SAV": "SAV",
    "Savannah": "SAV",

    "新泽西二号仓": "NJ",
    "新泽西": "NJ",
    "NJ": "NJ",
    "New Jersey": "NJ",
}


FIELD_ALIASES = {
    "仓库": ["仓库", "仓点", "仓库名称", "所属仓", "目的仓", "Warehouse"],
    "客户名称": ["客户名称", "客户", "客户名", "Customer", "Customer Name"],
    "产品渠道": ["产品渠道", "渠道", "T渠道", "服务渠道", "产品通道"],

    "出库时间": ["出库时间", "实际出库时间", "发车时间", "Outbound Time", "Ship Time"],
    "签收时间": ["签收时间", "实际签收时间", "POD时间", "妥投时间", "Delivered Time", "Delivery Time"],

    "提柜时间": ["提柜时间", "实际提柜时间", "提柜日期", "Pickup Time", "Pick Up Time"],
    "Available时间": ["Available时间", "AVAILABLE时间", "Available Time", "可提时间", "码头可提时间"],
    "实际抵仓时间": ["实际抵仓时间", "实际到仓时间", "抵仓时间", "到仓时间", "Actual Arrival Time", "Arrival Time"],
    "拆柜完成时间": ["拆柜完成时间", "拆柜结束时间", "拆柜完毕时间", "拆柜完成日期", "Unload Finish Time"],

    "目的地": ["目的地", "目的地址", "派送地址", "收货地址", "Destination"],
    "转仓地址": ["转仓地址", "中转地址", "转运地址", "Transfer Address"],

    "平台仓点": ["平台仓点", "平台仓", "平台仓库", "仓点名称"],
    "平台名称": ["平台名称", "平台", "平台类型"],

    "货型": ["货型", "货物类型", "装车类型", "货物形态"],
    "体积": ["体积", "方数", "CBM", "Volume", "出库体积"],
    "卡板数": ["卡板数", "板数", "托盘数", "Pallets", "出库卡板数"],
    "派送成本": ["派送成本", "成本", "Delivery Cost"],

    "车次号": ["车次号", "车次", "批次号", "批次", "Load No", "Trip No"],
    "工作单号": ["工作单号", "工作单", "运单号", "订单号", "SO", "SO号"],
    "柜号": ["柜号", "箱号", "Container", "Container No"],
}


PLATFORM_KEYWORDS = [
    "Walmart", "WalMart", "TiKToK", "TikTok", "SHEIN", "希音",
    "谷仓", "Wayfair", "万邑通", "运去哪", "乐歌", "盈仓"
]


EXCLUDE_FBX_KEYWORDS = [
    "Amazon", "AMAZON", "amazon",
    "商业地址", "私人地址", "住宅地址",
    "首页地址私人地址", "首页地址商业地址"
]


def clean_col_name(col):
    return (
        str(col)
        .strip()
        .replace("\n", "")
        .replace("\r", "")
        .replace(" ", "")
        .replace("　", "")
    )


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

    value = str(value).strip()

    return value in ["", "nan", "NaN", "None", "none", "null", "NULL", "-"]


def standardize_warehouse(value):
    if pd.isna(value):
        return np.nan

    value = str(value).strip()
    return WAREHOUSE_MAP.get(value, value)


def classify_customer_type(row):
    product_channel = row.get("产品渠道", np.nan)
    customer_name = row.get("客户名称", "")

    if is_blank(product_channel):
        return "美国本土客户"

    customer_name = "" if pd.isna(customer_name) else str(customer_name).strip()

    if customer_name == "深圳劲港跨境物流有限公司":
        return "联宇"

    if customer_name.startswith("联宇"):
        return "联宇"

    if customer_name.startswith("盈仓"):
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

    df["客户类型"] = df.apply(classify_customer_type, axis=1)
    df["T渠道类型"] = df["产品渠道"].apply(classify_t_channel)

    return df


def filter_warehouse(df, warehouse):
    df = df.copy()

    if warehouse == "四仓合并":
        if "仓库" in df.columns:
            return df[df["仓库"].isin(["LA", "NJ", "SAV", "DAL"])]
        return df

    if "仓库" in df.columns:
        return df[df["仓库"] == warehouse]

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
        df["统计周期"] = (
            df["周起始日"].dt.strftime("%Y-%m-%d")
            + " ~ "
            + df["周结束日"].dt.strftime("%Y-%m-%d")
        )

    df["统计周期"] = df["统计周期"].fillna("未知周期")

    return df


def ensure_numeric_cols(df, cols):
    df = df.copy()

    for col in cols:
        if col not in df.columns:
            df[col] = 0

        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    return df


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


def process_fba_warehouse_rank(df, warehouse, product_type, period_type):
    df = prepare_base_df(df)
    df = filter_warehouse(df, warehouse)

    if "出库时间" not in df.columns:
        raise ValueError("FBA仓点货量排行需要字段：出库时间")

    if "目的地" not in df.columns:
        raise ValueError("FBA仓点货量排行需要字段：目的地")

    df = add_period_column(df, period_type, "出库时间")

    destination = df["目的地"].astype(str)

    df["是否FBA仓点"] = destination.str.contains(
        "Amazon|AMAZON|amazon",
        na=False,
        regex=True
    )

    detail_df = df[df["是否FBA仓点"]].copy()

    detail_df["FBA仓点"] = detail_df["目的地"].apply(extract_fba_code)

    detail_df = ensure_numeric_cols(detail_df, ["体积", "卡板数", "派送成本"])

    result_df = (
        detail_df
        .groupby(["仓库", "统计周期", "FBA仓点"], dropna=False)
        .agg(
            票数=("原始行号", "count"),
            总体积=("体积", "sum"),
            总卡板数=("卡板数", "sum"),
            总派送成本=("派送成本", "sum")
        )
        .reset_index()
    )

    total_volume = result_df["总体积"].sum()
    total_pallets = result_df["总卡板数"].sum()
    total_tickets = result_df["票数"].sum()

    result_df["体积占比"] = result_df["总体积"] / total_volume if total_volume else np.nan
    result_df["卡板数占比"] = result_df["总卡板数"] / total_pallets if total_pallets else np.nan
    result_df["票数占比"] = result_df["票数"] / total_tickets if total_tickets else np.nan

    result_df = result_df.sort_values(
        ["统计周期", "总体积"],
        ascending=[True, False]
    )

    return detail_df, result_df


def process_fbx_platform_volume(df, warehouse, product_type, period_type):
    df = prepare_base_df(df)
    df = filter_warehouse(df, warehouse)

    if "出库时间" not in df.columns:
        raise ValueError("FBX平台仓点货量分析需要字段：出库时间")

    if "目的地" not in df.columns:
        raise ValueError("FBX平台仓点货量分析需要字段：目的地")

    df = add_period_column(df, period_type, "出库时间")

    if "转仓地址" in df.columns:
        df["原始目的地"] = df["目的地"]
        mask = df["转仓地址"].notna() & (df["转仓地址"].astype(str).str.strip() != "")

        # 避免 27、0、1 这种数字占位把真实目的地覆盖掉
        valid_transfer = mask & (~df["转仓地址"].astype(str).str.fullmatch(r"\d+"))

        df.loc[valid_transfer, "目的地"] = df.loc[valid_transfer, "转仓地址"]
    else:
        df["原始目的地"] = df["目的地"]

    df["修正后目的地"] = df["目的地"]

    destination = df["修正后目的地"].astype(str)

    is_excluded = destination.str.contains(
        "|".join(EXCLUDE_FBX_KEYWORDS),
        na=False,
        regex=True
    )

    is_platform = destination.str.contains(
        "|".join(PLATFORM_KEYWORDS),
        na=False,
        regex=True
    )

    detail_df = df[(~is_excluded) & is_platform].copy()

    detail_df["平台名称"] = detail_df["修正后目的地"].apply(extract_platform_name)
    detail_df["平台仓点"] = detail_df["修正后目的地"].astype(str).str.strip()

    detail_df = ensure_numeric_cols(detail_df, ["体积", "卡板数", "派送成本"])

    result_df = (
        detail_df
        .groupby(["仓库", "统计周期", "平台名称", "平台仓点"], dropna=False)
        .agg(
            票数=("原始行号", "count"),
            总体积=("体积", "sum"),
            总卡板数=("卡板数", "sum"),
            总派送成本=("派送成本", "sum")
        )
        .reset_index()
    )

    total_volume = result_df["总体积"].sum()
    total_pallets = result_df["总卡板数"].sum()
    total_tickets = result_df["票数"].sum()

    result_df["体积占比"] = result_df["总体积"] / total_volume if total_volume else np.nan
    result_df["卡板数占比"] = result_df["总卡板数"] / total_pallets if total_pallets else np.nan
    result_df["票数占比"] = result_df["票数"] / total_tickets if total_tickets else np.nan

    result_df = result_df.sort_values(
        ["统计周期", "总体积"],
        ascending=[True, False]
    )

    return detail_df, result_df


def process_delivery_timing(df, warehouse, product_type, period_type):
    df = prepare_base_df(df)
    df = filter_warehouse(df, warehouse)

    if "出库时间" not in df.columns or "签收时间" not in df.columns:
        raise ValueError("派送时效分析需要字段：出库时间、签收时间")

    df = add_period_column(df, period_type, "出库时间")

    df["出库时间"] = pd.to_datetime(df["出库时间"], errors="coerce")
    df["签收时间"] = pd.to_datetime(df["签收时间"], errors="coerce")

    df["派送时效"] = (df["签收时间"] - df["出库时间"]).dt.total_seconds() / 86400

    df["是否有效"] = df["派送时效"].between(0.01, 30, inclusive="both")

    detail_df = df.copy()
    detail_df.loc[~detail_df["是否有效"], "派送时效"] = np.nan

    result_df = (
        detail_df
        .groupby(["仓库", "统计周期"], dropna=False)
        .agg(
            总票数=("原始行号", "count"),
            有效票数=("是否有效", "sum"),
            平均派送时效=("派送时效", "mean"),
            P80派送时效=("派送时效", lambda x: x.dropna().quantile(0.8) if x.dropna().shape[0] else np.nan),
            P90派送时效=("派送时效", lambda x: x.dropna().quantile(0.9) if x.dropna().shape[0] else np.nan)
        )
        .reset_index()
    )

    result_df["有效数据占比"] = result_df["有效票数"] / result_df["总票数"]

    return detail_df, result_df


def process_pickup_timing(df, warehouse, product_type, period_type):
    df = prepare_base_df(df)
    df = filter_warehouse(df, warehouse)

    if "提柜时间" not in df.columns or "实际抵仓时间" not in df.columns:
        raise ValueError("提柜时效分析需要字段：提柜时间、实际抵仓时间")

    df["提柜时间"] = pd.to_datetime(df["提柜时间"], errors="coerce")
    df["实际抵仓时间"] = pd.to_datetime(df["实际抵仓时间"], errors="coerce")

    if "Available时间" in df.columns:
        df["Available时间"] = pd.to_datetime(df["Available时间"], errors="coerce")
    else:
        df["Available时间"] = pd.NaT

    df = add_period_column(df, period_type, "提柜时间")

    df["开始时间"] = np.where(
        df["仓库"].isin(["LA", "NJ", "SAV"]),
        df["Available时间"],
        df["提柜时间"]
    )

    df["开始时间"] = pd.to_datetime(df["开始时间"], errors="coerce")
    df["结束时间"] = df["实际抵仓时间"]

    df["提柜时效"] = (df["结束时间"] - df["开始时间"]).dt.total_seconds() / 86400
    df["是否有效"] = df["提柜时效"].between(0.01, 20, inclusive="both")

    detail_df = df.copy()
    detail_df.loc[~detail_df["是否有效"], "提柜时效"] = np.nan

    result_df = (
        detail_df
        .groupby(["仓库", "统计周期"], dropna=False)
        .agg(
            总票数=("原始行号", "count"),
            有效票数=("是否有效", "sum"),
            平均提柜时效=("提柜时效", "mean"),
            P80提柜时效=("提柜时效", lambda x: x.dropna().quantile(0.8) if x.dropna().shape[0] else np.nan),
            P90提柜时效=("提柜时效", lambda x: x.dropna().quantile(0.9) if x.dropna().shape[0] else np.nan)
        )
        .reset_index()
    )

    result_df["有效数据占比"] = result_df["有效票数"] / result_df["总票数"]

    return detail_df, result_df


def process_unload_timing(df, warehouse, product_type, period_type):
    df = prepare_base_df(df)
    df = filter_warehouse(df, warehouse)

    if "实际抵仓时间" not in df.columns or "拆柜完成时间" not in df.columns:
        raise ValueError("拆柜时效分析需要字段：实际抵仓时间、拆柜完成时间")

    df["实际抵仓时间"] = pd.to_datetime(df["实际抵仓时间"], errors="coerce")
    df["拆柜完成时间"] = pd.to_datetime(df["拆柜完成时间"], errors="coerce")

    df = add_period_column(df, period_type, "拆柜完成时间")

    df["拆柜时效"] = (df["拆柜完成时间"] - df["实际抵仓时间"]).dt.total_seconds() / 86400
    df["是否有效"] = df["拆柜时效"].between(0.01, 20, inclusive="both")

    detail_df = df.copy()
    detail_df.loc[~detail_df["是否有效"], "拆柜时效"] = np.nan

    result_df = (
        detail_df
        .groupby(["仓库", "统计周期"], dropna=False)
        .agg(
            总票数=("原始行号", "count"),
            有效票数=("是否有效", "sum"),
            平均拆柜时效=("拆柜时效", "mean"),
            P80拆柜时效=("拆柜时效", lambda x: x.dropna().quantile(0.8) if x.dropna().shape[0] else np.nan),
            P90拆柜时效=("拆柜时效", lambda x: x.dropna().quantile(0.9) if x.dropna().shape[0] else np.nan)
        )
        .reset_index()
    )

    result_df["有效数据占比"] = result_df["有效票数"] / result_df["总票数"]

    return detail_df, result_df


def process_uploaded_file(
    uploaded_file,
    sheet_name,
    warehouse,
    product_type,
    analysis_module,
    period_type
):
    uploaded_file.seek(0)
    df = pd.read_excel(uploaded_file, sheet_name=sheet_name)

    if analysis_module == "FBA仓点货量排行":
        detail_df, result_df = process_fba_warehouse_rank(
            df=df,
            warehouse=warehouse,
            product_type=product_type,
            period_type=period_type
        )

    elif analysis_module == "FBX平台仓点货量分析":
        detail_df, result_df = process_fbx_platform_volume(
            df=df,
            warehouse=warehouse,
            product_type=product_type,
            period_type=period_type
        )

    elif analysis_module == "派送时效分析":
        detail_df, result_df = process_delivery_timing(
            df=df,
            warehouse=warehouse,
            product_type=product_type,
            period_type=period_type
        )

    elif analysis_module == "提柜时效分析":
        detail_df, result_df = process_pickup_timing(
            df=df,
            warehouse=warehouse,
            product_type=product_type,
            period_type=period_type
        )

    elif analysis_module == "拆柜时效分析":
        detail_df, result_df = process_unload_timing(
            df=df,
            warehouse=warehouse,
            product_type=product_type,
            period_type=period_type
        )

    else:
        raise ValueError(f"暂不支持该分析模块：{analysis_module}")

    for col in result_df.select_dtypes(include=["float", "float64"]).columns:
        result_df[col] = result_df[col].round(4)

    return detail_df, result_df, analysis_module

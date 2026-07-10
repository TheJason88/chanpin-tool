import re
from datetime import datetime
from io import BytesIO

import pandas as pd
from openpyxl.utils import get_column_letter

import processors


FIELD_ALIASES = {
    "仓库": ["仓库", "入库仓库", "发货仓", "出库仓", "仓点", "warehouse", "Warehouse"],
    "批次号": ["批次号", "批次", "批次编号", "Batch", "Batch No", "batch_no"],
    "车次号": ["车次号", "派送车次号", "车次", "Trip", "Trip No"],
    "出库体积": ["出库体积", "出库方数", "方数", "体积", "CBM", "cbm", "Volume", "volume", "立方数", "出库CBM"],
    "出库卡板数": ["出库卡板数", "出库板数", "卡板数", "板数", "托盘数", "Pallets", "pallets", "Pallet", "pallet"],
    "派送成本": ["派送成本", "成本", "派送费用", "DeliveryCost", "Delivery Cost", "Cost", "cost"],
    "标准邮编": ["标准邮编", "目的地邮编", "邮编", "ZIP", "Zip", "zipcode", "ZipCode", "PostalCode", "Postal Code"],
    "目的州": ["目的州", "省/州", "州", "到达州", "目的地州", "State", "Destination State"],
    "平台名称": ["平台名称", "平台", "渠道", "客户平台"],
    "平台仓代码": ["平台仓代码", "仓库代码", "仓库Code", "仓点代码", "目的仓代码", "Warehouse Code"],
}

TRANSFER_WAREHOUSE_INFO = {
    "LA": {
        "display": "LA盈仓",
        "zip": "91708",
        "zip3": "917",
        "state": "CA",
        "line": "LA",
        "keywords": ["LA", "美西", "洛杉矶", "CHINO", "SAN ANTONIO"],
    },
    "NJ": {
        "display": "新泽西盈仓",
        "zip": "08857",
        "zip3": "088",
        "state": "NJ",
        "line": "LA-NJ",
        "keywords": ["NJ", "新泽西", "NEW JERSEY", "OLD BRIDGE", "JAKE BROWN"],
    },
    "SAV": {
        "display": "萨凡纳盈仓",
        "zip": "31408",
        "zip3": "314",
        "state": "GA",
        "line": "LA-SAV",
        "keywords": ["SAV", "萨凡纳", "SAVANNAH", "GARDEN CITY", "PROSPERITY"],
    },
    "DAL": {
        "display": "达拉斯盈仓",
        "zip": "75180",
        "zip3": "751",
        "state": "TX",
        "line": "LA-DAL",
        "keywords": ["DAL", "达拉斯", "DALLAS", "BALCH SPRINGS", "PEACHTREE"],
    },
}

INTEGER_OUTPUT_COLUMNS = ["排名", "车次数", "发车数", "派送数", "出库卡板数"]
DECIMAL_OUTPUT_COLUMNS = [
    "数值", "占比", "出库体积", "FBA出库体积", "FBX出库体积", "派送成本", "派送时效",
    "总出库体积", "总派送成本", "平均整车价", "每方平均价", "平均每车出库体积",
    "P80每车出库体积", "平均派送时效", "P80派送时效",
]

TEXT_COLUMN_KEYWORDS = ["邮编", "ZIP", "zip", "批次号", "车次号"]


def clean_header(value):
    return processors.clean_col_name(value)


def normalize_columns(df):
    out = df.copy()
    out.columns = [clean_header(c) for c in out.columns]
    return out


def alias_candidates(name):
    return FIELD_ALIASES.get(name, [name])


def find_first_column(df, canonical_name):
    clean_to_originals = {}
    for col in df.columns:
        clean_to_originals.setdefault(clean_header(col), []).append(col)
    for candidate in alias_candidates(canonical_name):
        clean = clean_header(candidate)
        originals = clean_to_originals.get(clean, [])
        if originals:
            return originals[0]
    return None


def normalize_numeric_series(series):
    text = series.astype(str).str.replace(",", "", regex=False).str.replace("$", "", regex=False)
    text = text.str.extract(r"(-?\d+(?:\.\d+)?)", expand=False)
    return pd.to_numeric(text, errors="coerce")


def best_numeric_alias(df, canonical_name):
    candidates = []
    clean_to_originals = {}
    for col in df.columns:
        clean_to_originals.setdefault(clean_header(col), []).append(col)
    for alias in alias_candidates(canonical_name):
        for col in clean_to_originals.get(clean_header(alias), []):
            if col not in candidates:
                candidates.append(col)
    if not candidates:
        return None
    scored = []
    for col in candidates:
        value_sum = float(normalize_numeric_series(df[col]).fillna(0).sum())
        scored.append((col, value_sum))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[0][0]


def repair_numeric_columns(df, columns=("出库体积", "出库卡板数", "派送成本")):
    out = normalize_columns(df)
    for target in columns:
        best_col = best_numeric_alias(out, target)
        if best_col is None:
            if target not in out.columns:
                out[target] = 0.0
            else:
                out[target] = normalize_numeric_series(out[target]).fillna(0).astype(float)
            continue
        target_sum = float(normalize_numeric_series(out[target]).fillna(0).sum()) if target in out.columns else 0.0
        best_sum = float(normalize_numeric_series(out[best_col]).fillna(0).sum())
        if target not in out.columns or target_sum <= 0 < best_sum:
            out[target] = normalize_numeric_series(out[best_col]).fillna(0).astype(float)
        else:
            out[target] = normalize_numeric_series(out[target]).fillna(0).astype(float)
    return out


def normalize_zip_value(value):
    zip_code, fix, valid, reason = processors.normalize_zip_value(value)
    return zip_code, fix, bool(valid), reason


def normalize_boolean_series(series):
    true_values = {"true", "1", "是", "yes", "y", "t"}
    return series.astype(str).str.strip().str.lower().isin(true_values)


def ensure_object_df(df):
    if df is None or df.empty:
        return df
    out = df.copy()
    for col in out.columns:
        out[col] = out[col].astype(object)
    return out


def split_values(value):
    if processors.is_blank(value):
        return []
    parts = re.split(r"[,，;；/\s]+", str(value))
    return [p.strip() for p in parts if p.strip() and p.strip().lower() not in ["nan", "none", "null", "false", "0"]]


def infer_transfer_target_from_text(text):
    upper = str(text).upper()
    for target, info in TRANSFER_WAREHOUSE_INFO.items():
        for keyword in info["keywords"]:
            if str(keyword).upper() in upper:
                return target, info
    return "", None


def infer_transfer_target_from_row(row):
    preferred_cols = ["调入仓库", "出库类型", "业务场景", "实际目的地", "修正后目的地", "目的地", "备注", "车次号", "批次号集合"]
    for col in preferred_cols:
        if col in row.index:
            target, info = infer_transfer_target_from_text(row.get(col, ""))
            if info:
                return target, info
    text = " ".join(str(row.get(c, "")) for c in preferred_cols if c in row.index)
    return infer_transfer_target_from_text(text)


def apply_dominant_destination_from_detail(cleaned_batches, detail_df):
    """同一FTL车次混多个目的地时，用该车次内出库体积最大的明细行覆盖目的地识别字段。"""
    if cleaned_batches is None or cleaned_batches.empty or detail_df is None or detail_df.empty:
        return cleaned_batches
    detail = repair_numeric_columns(detail_df, columns=("出库体积", "出库卡板数", "派送成本"))
    if "批次号" not in detail.columns:
        return cleaned_batches
    out = ensure_object_df(cleaned_batches)
    destination_cols = [
        "系统产品类型", "主产品类型", "平台名称", "平台仓代码集合", "平台仓配对集合",
        "FBA仓点代码集合", "标准邮编集合", "邮编前三位集合", "目的州", "邮编来源", "目的地邮编待补充",
    ]
    for col in destination_cols:
        if col not in out.columns:
            out[col] = ""

    for idx, row in out.iterrows():
        if str(row.get("主产品类型", "")) == "仓间调拨" or str(row.get("系统产品类型", "")) == "仓间调拨":
            continue
        batch_ids = split_values(row.get("批次号集合", row.get("批次号", "")))
        if not batch_ids:
            continue
        matched = detail[detail["批次号"].astype(str).isin(batch_ids)].copy()
        if matched.empty or "出库体积" not in matched.columns:
            continue
        matched["出库体积"] = pd.to_numeric(matched["出库体积"], errors="coerce").fillna(0)
        dominant = matched.sort_values("出库体积", ascending=False).iloc[0]

        product = str(dominant.get("FBA/FBX", dominant.get("系统产品类型", ""))).strip()
        if product not in ["FBA", "FBX"]:
            product = str(dominant.get("系统产品类型", row.get("主产品类型", ""))).strip()
        if product in ["FBA", "FBX"]:
            out.at[idx, "系统产品类型"] = product
            out.at[idx, "主产品类型"] = product

        zip_value = str(dominant.get("标准邮编", "")).strip()
        zip3_value = str(dominant.get("邮编前三位", "")).strip()
        state_value = str(dominant.get("目的州", "")).strip()
        if zip_value.lower() in ["nan", "none", "<na>"]:
            zip_value = ""
        if zip3_value.lower() in ["nan", "none", "<na>"]:
            zip3_value = ""
        if state_value.lower() in ["nan", "none", "<na>"]:
            state_value = ""

        out.at[idx, "标准邮编集合"] = zip_value
        out.at[idx, "邮编前三位集合"] = zip3_value or (zip_value[:3] if len(zip_value) == 5 else "")
        out.at[idx, "目的州"] = state_value
        out.at[idx, "邮编来源"] = str(dominant.get("邮编来源", "最大方数目的地覆盖")).strip() or "最大方数目的地覆盖"
        out.at[idx, "目的地邮编待补充"] = len(split_values(zip_value)) == 0

        fba_code = str(dominant.get("FBA仓点代码", "")).strip()
        platform = str(dominant.get("平台名称", "")).strip()
        platform_code = str(dominant.get("平台仓代码", dominant.get("仓库代码", ""))).strip()
        if product == "FBA":
            out.at[idx, "FBA仓点代码集合"] = "" if fba_code.lower() in ["nan", "none", "<na>"] else fba_code
            out.at[idx, "平台名称"] = ""
            out.at[idx, "平台仓代码集合"] = ""
            out.at[idx, "平台仓配对集合"] = ""
        elif product == "FBX":
            out.at[idx, "FBA仓点代码集合"] = ""
            out.at[idx, "平台名称"] = "" if platform.lower() in ["nan", "none", "<na>"] else platform
            out.at[idx, "平台仓代码集合"] = "" if platform_code.lower() in ["nan", "none", "<na>"] else platform_code
            if platform and platform_code and platform.lower() not in ["nan", "none"] and platform_code.lower() not in ["nan", "none"]:
                out.at[idx, "平台仓配对集合"] = f"{platform}||{platform_code}"
    return out


def clean_for_excel_output(df, sheet_type=""):
    if df is None:
        return pd.DataFrame()
    out = df.copy()
    for col in out.columns:
        if any(k in str(col) for k in TEXT_COLUMN_KEYWORDS):
            out[col] = out[col].fillna("").astype(str).replace({"nan": "", "None": "", "<NA>": ""})
    for col in INTEGER_OUTPUT_COLUMNS:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").round(0).astype("Int64")
    if sheet_type == "发车量" and "数值" in out.columns:
        out["数值"] = pd.to_numeric(out["数值"], errors="coerce").round(0).astype("Int64")
    for col in DECIMAL_OUTPUT_COLUMNS:
        if col in out.columns:
            if sheet_type == "发车量" and col == "数值":
                continue
            out[col] = pd.to_numeric(out[col], errors="coerce").round(2)
    return out


def _format_excel_ws(ws):
    if ws.max_row < 1:
        return
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for cell in ws[1]:
        header = str(cell.value) if cell.value is not None else ""
        if any(k.upper() in header.upper() for k in TEXT_COLUMN_KEYWORDS):
            for row in range(1, ws.max_row + 1):
                ws.cell(row=row, column=cell.column).number_format = "@"
    for col_idx, col_cells in enumerate(ws.columns, start=1):
        max_len = 0
        for cell in col_cells:
            value = cell.value
            if value is None:
                continue
            max_len = max(max_len, len(str(value)))
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max(max_len + 2, 10), 40)


def write_sheets_to_excel(sheets):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for sheet_name, df in sheets.items():
            safe_name = str(sheet_name)[:31]
            cleaned = clean_for_excel_output(df, sheet_type=safe_name)
            cleaned.to_excel(writer, index=False, sheet_name=safe_name)
            _format_excel_ws(writer.book[safe_name])
    output.seek(0)
    return output


def safe_filename_part(value):
    text = str(value or "").strip()
    text = re.sub(r"[\\/:*?\"<>|\s]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "未命名"


def build_output_filename(warehouse, module_name, *descriptors, ext="xlsx"):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    parts = [warehouse, module_name]
    parts.extend([d for d in descriptors if d and str(d) not in ["请填入", "不适用", "全部"]])
    parts.append(timestamp)
    safe_parts = [safe_filename_part(p) for p in parts]
    return "_".join(safe_parts) + f".{ext}"

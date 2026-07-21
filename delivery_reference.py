import re
from pathlib import Path

import pandas as pd

import processors


BASE_DIR = Path(__file__).resolve().parent
REFERENCE_DIR = BASE_DIR / "reference_data"
FBA_ZIP_PATH = REFERENCE_DIR / "delivery_fba_zip.csv"
PLATFORM_ZIP_PATH = REFERENCE_DIR / "delivery_platform_zip.csv"


def _read_reference_csv(path):
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype=str).fillna("")


def _zip5(value):
    if processors.is_blank(value):
        return ""
    text = str(value).strip()
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    zip4 = re.search(r"\b(\d{5})-\d{4}\b", text)
    if zip4:
        return zip4.group(1)
    five = re.search(r"(?<!\d)(\d{5})(?!\d)", text)
    if five:
        return five.group(1)
    four = re.search(r"(?<!\d)(\d{4})(?!\d)", text)
    if four:
        return "0" + four.group(1)
    return ""


def _norm(value):
    return re.sub(r"[^A-Z0-9]+", "", str(value).upper())


def _truthy(value):
    return str(value).strip().lower() in ["true", "1", "是", "yes", "y"] or value is True


def load_fba_reference():
    df = _read_reference_csv(FBA_ZIP_PATH)
    if df.empty:
        return df, {}
    df["FBA仓点代码"] = df["FBA仓点代码"].astype(str).str.upper().str.strip()
    df["邮编"] = df["邮编"].apply(_zip5)
    if "邮编前三位" not in df.columns:
        df["邮编前三位"] = df["邮编"].str[:3]
    fba_map = {}
    for _, row in df.iterrows():
        code = str(row.get("FBA仓点代码", "")).upper().strip()
        if code:
            fba_map[code] = row.to_dict()
    return df, fba_map


def load_platform_reference():
    df = _read_reference_csv(PLATFORM_ZIP_PATH)
    if df.empty:
        return df, []
    df["邮编"] = df["邮编"].apply(_zip5)
    df["平台_标准化"] = df["平台_标准化"].astype(str).str.strip()
    df["仓库代码"] = df["仓库代码"].astype(str).str.strip()
    df["代码匹配Key"] = df["仓库代码"].apply(_norm)
    df["邮编匹配Key"] = df["邮编"].astype(str).str.strip()
    records = []
    for _, row in df.iterrows():
        data = row.to_dict()
        # 越长的仓库代码优先，避免短代码误命中。
        data["_match_len"] = len(data.get("代码匹配Key", ""))
        records.append(data)
    records.sort(key=lambda x: x.get("_match_len", 0), reverse=True)
    return df, records


FBA_REFERENCE_DF, FBA_REFERENCE_MAP = load_fba_reference()
PLATFORM_REFERENCE_DF, PLATFORM_REFERENCE_RECORDS = load_platform_reference()


def match_fba_reference(destination, existing_code=""):
    code = str(existing_code or "").upper().strip()
    if not code:
        code = processors.extract_fba_code(destination)
    if not code:
        return None
    ref = FBA_REFERENCE_MAP.get(code)
    if ref:
        return {
            "类型": "FBA仓点",
            "代码": code,
            "邮编": _zip5(ref.get("邮编", "")),
            "州": str(ref.get("州", "")).upper().strip(),
            "名称": ref.get("站点名称", ""),
            "匹配方式": "内置FBA仓点邮编表",
        }
    return None


def match_platform_reference(destination, existing_platform=""):
    if not PLATFORM_REFERENCE_RECORDS:
        return None
    text = "" if processors.is_blank(destination) else str(destination)
    text_norm = _norm(text)
    platform = existing_platform if not processors.is_blank(existing_platform) else processors.extract_platform_name(text)
    platform_norm = str(platform).strip().lower()

    # 1. 仓库代码精确/灵活命中。
    for rec in PLATFORM_REFERENCE_RECORDS:
        code_key = rec.get("代码匹配Key", "")
        if code_key and len(code_key) >= 4 and code_key in text_norm:
            return {
                "类型": "平台仓",
                "代码": rec.get("仓库代码", ""),
                "平台": rec.get("平台_标准化", ""),
                "邮编": _zip5(rec.get("邮编", "")),
                "州": str(rec.get("州", "")).upper().strip(),
                "匹配方式": "内置平台仓邮编表-仓库代码匹配",
            }

    # 2. 平台 + 邮编命中。
    extracted_zip, _, zip_valid, _ = processors.normalize_zip_value(text)
    if zip_valid:
        for rec in PLATFORM_REFERENCE_RECORDS:
            rec_platform = str(rec.get("平台_标准化", "")).strip().lower()
            if rec.get("邮编匹配Key") == extracted_zip and (not platform_norm or rec_platform == platform_norm):
                return {
                    "类型": "平台仓",
                    "代码": rec.get("仓库代码", ""),
                    "平台": rec.get("平台_标准化", ""),
                    "邮编": _zip5(rec.get("邮编", "")),
                    "州": str(rec.get("州", "")).upper().strip(),
                    "匹配方式": "内置平台仓邮编表-平台邮编匹配",
                }

    return None


def apply_delivery_reference_memory(df):
    """
    把内置 FBA仓点邮编表 + 平台仓邮编表应用到派送一/派送二流程。
    目标：自动补齐 FBA/平台仓目的地邮编、州和匹配来源；商业/私人地址仍通过第二步批次匹配补充。
    """
    if df is None or df.empty:
        return df

    out = df.copy()
    for col in ["标准邮编", "邮编前三位", "邮编来源", "邮编修正类型", "邮编是否有效", "邮编异常原因", "目的州", "FBX代码", "规则匹配类型", "规则匹配代码", "规则匹配邮编", "规则匹配州", "规则匹配方式"]:
        if col not in out.columns:
            out[col] = ""

    for idx, row in out.iterrows():
        destination = row.get("修正后目的地", row.get("目的地", ""))
        existing_zip_valid = _truthy(row.get("邮编是否有效"))
        ref = None

        # FBA优先。
        system_product_type = str(row.get("系统产品类型", ""))
        if system_product_type == "FBA" or "AMAZON" in str(destination).upper():
            ref = match_fba_reference(destination, row.get("FBA仓点代码", ""))

        # 平台仓其次。
        if ref is None and (system_product_type == "FBX平台仓" or processors.extract_platform_name(destination) != "非平台/未知"):
            ref = match_platform_reference(destination, row.get("平台名称", ""))

        if not ref:
            continue

        ref_zip = _zip5(ref.get("邮编", ""))
        ref_state = str(ref.get("州", "")).upper().strip()
        if ref_zip:
            out.at[idx, "标准邮编"] = ref_zip
            out.at[idx, "邮编前三位"] = ref_zip[:3]
            out.at[idx, "邮编来源"] = ref.get("匹配方式", "内置规则表")
            out.at[idx, "邮编修正类型"] = "规则表补充"
            out.at[idx, "邮编是否有效"] = True
            out.at[idx, "邮编异常原因"] = ""
        elif not existing_zip_valid:
            out.at[idx, "邮编是否有效"] = False

        if ref_state:
            out.at[idx, "目的州"] = ref_state
        out.at[idx, "规则匹配类型"] = ref.get("类型", "")
        out.at[idx, "规则匹配代码"] = ref.get("代码", "")
        out.at[idx, "规则匹配邮编"] = ref_zip
        out.at[idx, "规则匹配州"] = ref_state
        out.at[idx, "规则匹配方式"] = ref.get("匹配方式", "")

        if ref.get("类型") == "FBA仓点":
            out.at[idx, "FBA仓点代码"] = ref.get("代码", "")
        if ref.get("类型") == "平台仓":
            out.at[idx, "平台名称"] = ref.get("平台", row.get("平台名称", ""))
            out.at[idx, "FBX代码"] = ref.get("代码", row.get("FBX代码", ""))

    out["标准邮编"] = out["标准邮编"].fillna("").astype(str)
    out.loc[out["标准邮编"].isin(["nan", "None", "<NA>", "00000"]), "标准邮编"] = ""
    out["邮编前三位"] = out["标准邮编"].apply(lambda x: str(x)[:3] if len(str(x)) == 5 else "")
    out["目的地邮编待补充"] = ~out["邮编是否有效"].apply(_truthy)

    if "专线线路" in out.columns or "专线识别方式" in out.columns:
        out = out.drop(columns=["专线线路", "专线识别方式"], errors="ignore")
    line_results = out.apply(processors.identify_delivery_line, axis=1, result_type="expand")
    line_results.columns = ["专线线路", "专线识别方式"]
    out = pd.concat([out, line_results], axis=1)

    if "出库时间" in out.columns:
        out["出库时间"] = pd.to_datetime(out["出库时间"], errors="coerce")
        out = out.sort_values(["目的地邮编待补充", "出库时间"], ascending=[True, True]).reset_index(drop=True)
    return out

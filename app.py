from datetime import date

import pandas as pd
import streamlit as st

st.set_page_config(page_title="美盈产品数据处理工具", layout="wide")
st.title("美盈产品数据处理工具")
st.write("请选择分析维度并上传对应 Excel，系统将自动清洗、筛选、汇总并生成结果文件。")
st.divider()

# 页面先稳定打开；业务模块导入失败时在页面内显示具体错误。
_dependency_error = None
try:
    import processors
    import delivery_reference
    import delivery_workflow
    import delivery_match_adapter
    import delivery_stage1_adapter
    import tool_common
    import delivery_runtime
except Exception as exc:
    _dependency_error = exc

if _dependency_error is not None:
    st.error("工具启动失败：基础模块导入失败。页面已进入保护模式，下面是具体错误。")
    st.exception(_dependency_error)
    st.stop()

VALID_WAREHOUSES = ["LA", "NJ", "SAV", "DAL"]
PLACEHOLDER = "请填入"
DELIVERY_STAGE1_MODULE = "派送原数据处理"
DELIVERY_STAGE2_MODULE = "派送数据匹配及分析"
NORMAL_MODULES = ["货量分析", "提柜分析", "拆柜分析"]
DEFAULT_PRODUCT_TYPE = "全部"
DESTINATION_TYPES = ["全部", "FBA", "FBX"]

_bootstrap_error = None
try:
    # 不在每次下拉框变化时强制 reload。Streamlit rerun 只重新执行页面逻辑，模块初始化保持轻量。
    delivery_runtime.bootstrap(delivery_workflow)
except Exception as exc:
    _bootstrap_error = exc

if _bootstrap_error is not None:
    st.error("工具启动失败：派送运行时规则加载失败。页面已进入保护模式，下面是具体错误。")
    st.exception(_bootstrap_error)
    st.stop()


def _is_blank(value):
    return processors.is_blank(value)


def _text(value):
    if _is_blank(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in ["nan", "none", "null", "<na>"] else text


def _numeric_value(value):
    value = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return 0.0 if pd.isna(value) else float(value)


def classify_delivery_destination_type(row):
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
    if ("FBA" in product_text or "AMAZON" in product_text) and "FBX" not in product_text:
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
    if cleaned_batches is None or cleaned_batches.empty or "目的地邮编待补充" not in cleaned_batches.columns:
        return pd.DataFrame()
    mask = tool_common.normalize_boolean_series(cleaned_batches["目的地邮编待补充"])
    return cleaned_batches[mask].copy()


def get_stage2_report_sheet_names(destination_type="全部"):
    if destination_type == "FBA":
        return ["货量", "FBA货量排行", "发车量", "派送时效", "成本", "派送二_匹配后合并数据", "邮编异常审核", "区域识别规则", "干线识别规则"]
    if destination_type == "FBX":
        return ["货量", "FBX平台仓货量", "发车量", "派送时效", "成本", "派送二_匹配后合并数据", "邮编异常审核", "区域识别规则", "干线识别规则"]
    return ["货量", "FBA货量排行", "FBX平台仓货量", "发车量", "派送时效", "成本", "派送二_匹配后合并数据", "邮编异常审核", "区域识别规则", "干线识别规则"]


def _split_combined_report(combined):
    if combined is None or combined.empty:
        empty = pd.DataFrame()
        return empty, empty, empty
    volume = combined[combined["报告部分"].astype(str).str.startswith("1.")].copy()
    volume = volume[~volume["指标名称"].astype(str).isin(["FBA仓点货量排行", "FBX平台仓货量排行"])]
    dispatch = combined[combined["报告部分"].astype(str).str.startswith("2.")].copy()
    timing = combined[combined["报告部分"].astype(str).str.startswith("3.")].copy()
    return volume, dispatch, timing


def build_stage2_report_for_destination(cleaned_batches, match_df=None, period_type="按周统计", destination_type="全部"):
    matched = delivery_workflow.prepare_stage2_for_report(cleaned_batches, match_df, period_type)
    matched = filter_delivery_destination_type(matched, destination_type)

    combined = delivery_workflow.build_sheet1_volume_dispatch_time_report(matched)
    volume, dispatch, timing = _split_combined_report(combined)
    cost = delivery_match_adapter.build_station_cost_report(matched)
    zip_audit = rebuild_zip_audit_from_cleaned(matched)

    report = {"货量": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(volume, "货量"), "货量")}
    if destination_type in ["全部", "FBA"]:
        report["FBA货量排行"] = delivery_match_adapter._safe_round(
            delivery_match_adapter._finalize_sheet(delivery_match_adapter.build_fba_rank_sheet(matched), "FBA货量排行"),
            "FBA货量排行",
        )
    if destination_type in ["全部", "FBX"]:
        report["FBX平台仓货量"] = delivery_match_adapter._safe_round(
            delivery_match_adapter._finalize_sheet(delivery_match_adapter.build_fbx_platform_warehouse_sheet(matched), "FBX平台仓货量"),
            "FBX平台仓货量",
        )
    report.update({
        "发车量": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(dispatch, "发车量"), "发车量"),
        "派送时效": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(timing, "派送时效"), "派送时效"),
        "成本": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(cost, "成本"), "成本"),
        "派送二_匹配后合并数据": delivery_match_adapter._safe_round(delivery_match_adapter._finalize_sheet(matched, "明细"), "明细"),
        "邮编异常审核": delivery_match_adapter._finalize_zip_audit_sheet(zip_audit),
        "区域识别规则": delivery_workflow.REGION_RULES_DF,
        "干线识别规则": delivery_workflow.LINEHAUL_RULES,
    })

    ordered = {}
    for sheet in get_stage2_report_sheet_names(destination_type):
        if sheet in report:
            ordered[sheet] = report[sheet]
    return ordered


def selected_date_range_is_valid(value):
    return isinstance(value, (tuple, list)) and len(value) == 2 and value[0] is not None and value[1] is not None


def validate_uploaded_warehouse_for_df(df, selected_warehouse):
    preview_df = processors.normalize_columns(df)
    if "仓库" not in preview_df.columns:
        if selected_warehouse == "全部":
            raise ValueError("仓点选择为“全部”，但文件中没有识别到入库仓库/发货仓/仓库字段，无法按 LA/DAL/NJ/SAV 分仓分析。")
        return
    raw_values = sorted([str(x) for x in preview_df["仓库"].dropna().unique()])
    standardized = preview_df["仓库"].apply(processors.standardize_warehouse)
    actual_known = sorted([str(x) for x in standardized.dropna().unique() if str(x) in VALID_WAREHOUSES])
    if selected_warehouse == "全部":
        if not actual_known:
            raise ValueError(f"仓点选择为“全部”，但文件仓库值无法映射到 LA/DAL/NJ/SAV。文件仓库值：{raw_values}")
        return
    if selected_warehouse in VALID_WAREHOUSES:
        if not actual_known:
            return
        if set(actual_known) != {selected_warehouse}:
            raise ValueError(f"仓点选择不匹配：页面选择的是 {selected_warehouse}，但文件仓库识别为 {actual_known}。请改选正确仓点，或上传对应仓点的数据源。")


def validate_uploaded_warehouse(uploaded_file, sheet_name, selected_warehouse):
    uploaded_file.seek(0)
    preview_df = pd.read_excel(uploaded_file, sheet_name=sheet_name)
    validate_uploaded_warehouse_for_df(preview_df, selected_warehouse)


def read_first_sheet(uploaded_file):
    uploaded_file.seek(0)
    xls = pd.ExcelFile(uploaded_file)
    sheet_name = xls.sheet_names[0]
    uploaded_file.seek(0)
    return pd.read_excel(uploaded_file, sheet_name=sheet_name, dtype=str)


def combine_uploaded_match_files(match_files):
    if not match_files:
        return pd.DataFrame(), "未上传6B匹配文件"
    frames = []
    signatures = []
    for file in match_files:
        df = read_first_sheet(file)
        df = processors.normalize_columns(df.copy())
        df["来源匹配文件"] = getattr(file, "name", "匹配文件")
        frames.append(df)
        signatures.append(tuple([c for c in df.columns if c != "来源匹配文件"]))
    same_structure = len(set(signatures)) == 1
    combined = pd.concat(frames, ignore_index=True, sort=False)
    if same_structure:
        message = f"已合并 {len(match_files)} 个6B匹配文件；文件结构完全一致，已按行纵向合并。"
    else:
        message = f"已合并 {len(match_files)} 个6B匹配文件；文件结构不完全一致，已按字段并集合并，并保留来源匹配文件列。"
    return combined, message


def sanitize_stage2_input_df(df):
    return tool_common.ensure_object_df(df)


warehouse = st.selectbox("1. 选择仓点", [PLACEHOLDER, "LA", "NJ", "SAV", "DAL", "全部"], index=0, key="warehouse_select")
analysis_module = st.selectbox(
    "2. 选择分析模块",
    [PLACEHOLDER, "货量分析", "提柜分析", "拆柜分析", DELIVERY_STAGE1_MODULE, DELIVERY_STAGE2_MODULE],
    index=0,
    key="analysis_module_select",
)

product_type = DEFAULT_PRODUCT_TYPE
delivery_destination_type = "全部"
if analysis_module in [DELIVERY_STAGE1_MODULE, DELIVERY_STAGE2_MODULE]:
    delivery_destination_type = st.selectbox("3. 选择目的地类型", DESTINATION_TYPES, index=0, key="delivery_destination_type_select")

period_type = "不适用"
date_range = None
if analysis_module == DELIVERY_STAGE1_MODULE:
    st.info("派送原数据处理执行全量清洗，不按时间范围筛选；可按目的地类型筛选：全部 / FBA / FBX。")
elif analysis_module == DELIVERY_STAGE2_MODULE:
    period_type = st.selectbox("4. 选择统计周期", [PLACEHOLDER, "按月统计", "按周统计"], index=0, key="stage2_period_select")
    st.info("派送数据匹配及分析会先完成邮编/平台仓匹配，再按目的地类型生成报告。选择全部时输出FBA和FBX专项表；选择FBA/FBX时只输出对应目的地的专项表。")
elif analysis_module in NORMAL_MODULES or analysis_module == PLACEHOLDER:
    period_type = st.selectbox("3. 选择统计周期", [PLACEHOLDER, "按月统计", "按周统计"], index=0, key="normal_period_select")
    today = date.today()
    month_start = today.replace(day=1)
    date_range = st.date_input("4. 选择时间范围", value=(month_start, today), format="YYYY-MM-DD", key="normal_date_range")

st.caption(
    "说明：货量=ETA；提柜=实际抵仓时间；拆柜=拆柜完成时间。"
    "普通模块不做产品类型筛选，统一按上传文件中的全部有效数据处理。"
    "派送模块支持目的地类型：全部 / FBA / FBX；FBA=Amazon/FBA仓，FBX=非FBA目的地。"
    "派送二选择FBA时不输出FBX平台仓货量；选择FBX时不输出FBA货量排行；选择全部时两类专项表均输出。"
    "6B支持多文件上传；结构完全相同的匹配文件默认纵向合并。"
    "邮编异常审核表请填写“补充标准邮编”，可选填写“补充目的州”。"
)

selection_complete = warehouse != PLACEHOLDER and analysis_module != PLACEHOLDER
if analysis_module in NORMAL_MODULES or analysis_module == PLACEHOLDER:
    selection_complete = selection_complete and period_type != PLACEHOLDER
elif analysis_module == DELIVERY_STAGE2_MODULE:
    selection_complete = selection_complete and period_type != PLACEHOLDER

if analysis_module == DELIVERY_STAGE1_MODULE:
    with st.form("delivery_stage1_form", clear_on_submit=False):
        raw_files = st.file_uploader(
            "4. 上传派送原始数据文件（可多选，格式需一致）",
            type=["xlsx", "xls"],
            accept_multiple_files=True,
            key="delivery_stage1_raw_files",
        )
        run_stage1 = st.form_submit_button("开始处理派送原数据", type="primary")
    if raw_files:
        st.success(f"已上传 {len(raw_files)} 个文件。")
    if run_stage1:
        try:
            if not selection_complete:
                st.warning("请先把仓点、分析模块选择完整。")
            elif not raw_files:
                st.warning("请至少上传一个派送原始数据文件。")
            else:
                file_dfs = []
                for file in raw_files:
                    file.seek(0)
                    xls = pd.ExcelFile(file)
                    sheet_name = xls.sheet_names[0]
                    file.seek(0)
                    df = pd.read_excel(file, sheet_name=sheet_name)
                    validate_uploaded_warehouse_for_df(df, warehouse)
                    file_dfs.append((file.name, df))
                warehouse_for_processing = "四仓合并" if warehouse == "全部" else warehouse
                cleaned_batches, invalid_detail, zip_audit_df, raw_detail = delivery_workflow.process_stage1_raw_files_to_cleaned_batches(
                    file_dfs=file_dfs,
                    warehouse=warehouse_for_processing,
                    period_type="不适用",
                    start_date=None,
                    end_date=None,
                )
                cleaned_batches = filter_delivery_destination_type(cleaned_batches, delivery_destination_type)
                zip_audit_df = rebuild_zip_audit_from_cleaned(cleaned_batches)

                st.subheader("清洗后数据预览")
                st.dataframe(cleaned_batches.head(100), use_container_width=True)
                st.subheader("邮编异常数据预览")
                st.dataframe(zip_audit_df.head(100), use_container_width=True)
                st.subheader("无效数据预览")
                st.dataframe(invalid_detail.head(100), use_container_width=True)
                output = tool_common.write_sheets_to_excel({"清洗后数据": cleaned_batches, "邮编异常数据": zip_audit_df, "无效数据": invalid_detail})
                file_name = tool_common.build_output_filename(warehouse, DELIVERY_STAGE1_MODULE, delivery_destination_type)
                st.download_button("下载派送一结果 Excel", output, file_name, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="download_delivery_stage1")
        except Exception as e:
            st.error("派送原数据处理失败，请检查文件字段、仓点选择或源数据格式。")
            st.exception(e)

elif analysis_module == DELIVERY_STAGE2_MODULE:
    stage1_file = st.file_uploader("5A. 上传派送一结果，或上传已补充邮编异常审核的派送二报告", type=["xlsx", "xls"], key="stage1_result_file")
    match_files = st.file_uploader(
        "5B. 上传人工匹配文件（可选，可多选；鲲运导出列表也可，需含批次号 + 邮编/目的地邮编/标准邮编，可含省/州）",
        type=["xlsx", "xls"],
        accept_multiple_files=True,
        key="manual_match_file",
    )
    if match_files:
        st.success(f"5B已上传 {len(match_files)} 个匹配文件。结构相同会自动按行合并。")

    if st.button("开始匹配并生成派送分析报告", type="primary", key="run_delivery_stage2"):
        try:
            if not selection_complete:
                st.warning("请先把仓点、分析模块、统计周期都选择完整。")
            elif not stage1_file:
                st.warning("请先上传派送一结果或已补充邮编异常审核的派送二报告。")
            else:
                cleaned_batches = delivery_workflow.read_stage1_cleaned_batches(stage1_file)
                cleaned_batches = sanitize_stage2_input_df(cleaned_batches)
                match_df, merge_message = combine_uploaded_match_files(match_files)
                match_df = sanitize_stage2_input_df(match_df)
                if match_files:
                    st.info(merge_message)
                if delivery_destination_type == "全部":
                    metrics = delivery_workflow.process_stage2_analysis(cleaned_batches, match_df, period_type=period_type)
                else:
                    metrics = build_stage2_report_for_destination(cleaned_batches, match_df=match_df, period_type=period_type, destination_type=delivery_destination_type)
                report_sheets = list(metrics.keys())
                st.success("派送分析报告已生成，详细结果请下载Excel查看。")
                st.write(f"报告结构：{'、'.join(report_sheets)}。当前目的地类型：{delivery_destination_type}")
                output = tool_common.write_sheets_to_excel(metrics)
                file_name = tool_common.build_output_filename(warehouse, DELIVERY_STAGE2_MODULE, delivery_destination_type, period_type)
                st.download_button("下载派送分析报告 Excel", output, file_name, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="download_delivery_stage2")
        except Exception as e:
            st.error("派送数据匹配及分析失败，请检查派送一结果文件、匹配文件字段或批次号是否一致。")
            st.exception(e)

else:
    uploaded_file = st.file_uploader("5. 上传 Excel", type=["xlsx", "xls"], key="normal_uploaded_file")
    if uploaded_file is not None:
        try:
            uploaded_file.seek(0)
            excel_file = pd.ExcelFile(uploaded_file)
            sheet_names = excel_file.sheet_names
            sheet_name = st.selectbox("选择工作表", sheet_names, key="normal_sheet_select") if len(sheet_names) > 1 else sheet_names[0]
            st.success(f"文件上传成功，当前工作表：{sheet_name}")
            if st.button("开始分析", type="primary", key="run_normal_analysis"):
                if not selection_complete:
                    st.warning("请先把仓点、分析模块、统计周期都选择完整。")
                elif not selected_date_range_is_valid(date_range):
                    st.warning("请选择完整的开始日期和结束日期。")
                elif date_range[0] > date_range[1]:
                    st.warning("开始日期不能晚于结束日期。")
                else:
                    validate_uploaded_warehouse(uploaded_file, sheet_name, warehouse)
                    uploaded_file.seek(0)
                    warehouse_for_processing = "四仓合并" if warehouse == "全部" else warehouse
                    detail_df, result_df, final_module = processors.process_uploaded_file(
                        uploaded_file=uploaded_file,
                        sheet_name=sheet_name,
                        warehouse=warehouse_for_processing,
                        product_type=DEFAULT_PRODUCT_TYPE,
                        analysis_module=analysis_module,
                        period_type=period_type,
                        start_date=date_range[0],
                        end_date=date_range[1],
                    )
                    st.subheader("数据处理结果")
                    st.dataframe(result_df, use_container_width=True)
                    st.subheader("清洗后的数据集预览")
                    st.dataframe(detail_df.head(100), use_container_width=True)
                    output = tool_common.write_sheets_to_excel({"清洗后的数据集": detail_df, "数据处理结果": result_df})
                    file_name = tool_common.build_output_filename(warehouse, final_module, period_type)
                    st.download_button("下载结果 Excel", output, file_name, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="download_normal_result")
        except Exception as e:
            st.error("处理失败，请检查文件字段、工作表、时间范围或分析模块是否匹配。")
            st.exception(e)
    else:
        st.info("请先完成选择，并上传 Excel 文件。")

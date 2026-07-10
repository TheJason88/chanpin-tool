import importlib
from datetime import date

import pandas as pd
import streamlit as st

import processors
import delivery_reference
import delivery_workflow
import delivery_match_adapter
import delivery_stage1_adapter
import tool_common
import delivery_runtime

# Streamlit rerun sometimes keeps imported modules in memory.
importlib.reload(processors)
importlib.reload(delivery_reference)
importlib.reload(delivery_workflow)
importlib.reload(delivery_match_adapter)
importlib.reload(delivery_stage1_adapter)
importlib.reload(tool_common)
importlib.reload(delivery_runtime)
delivery_runtime.bootstrap(delivery_workflow)

VALID_WAREHOUSES = ["LA", "NJ", "SAV", "DAL"]
PLACEHOLDER = "请填入"
DELIVERY_STAGE1_MODULE = "派送原数据处理"
DELIVERY_STAGE2_MODULE = "派送数据匹配及分析"
NORMAL_MODULES = ["货量分析", "提柜分析", "拆柜分析"]
DEFAULT_PRODUCT_TYPE = "全部"

st.set_page_config(page_title="美盈产品数据处理工具", layout="wide")
st.title("美盈产品数据处理工具")
st.write("请选择分析维度并上传对应 Excel，系统将自动清洗、筛选、汇总并生成结果文件。")
st.divider()

warehouse = st.selectbox("1. 选择仓点", [PLACEHOLDER, "LA", "NJ", "SAV", "DAL", "全部"], index=0)
analysis_module = st.selectbox(
    "2. 选择分析模块",
    [PLACEHOLDER, "货量分析", "提柜分析", "拆柜分析", DELIVERY_STAGE1_MODULE, DELIVERY_STAGE2_MODULE],
    index=0,
)

# 产品类型筛选已移除：目前各功能没有可靠依据按页面选择的产品类型筛数据，统一按全部数据处理。
product_type = DEFAULT_PRODUCT_TYPE

period_type = "不适用"
date_range = None
if analysis_module == DELIVERY_STAGE1_MODULE:
    st.info("派送原数据处理执行全量清洗，不按时间范围筛选，也不按产品类型筛选。")
elif analysis_module == DELIVERY_STAGE2_MODULE:
    period_type = st.selectbox("3. 选择统计周期", [PLACEHOLDER, "按月统计", "按周统计"], index=0)
    st.info("派送数据匹配及分析按统计周期生成报告；产品类型由目的地规则自动识别，不使用页面筛选。")
elif analysis_module in NORMAL_MODULES or analysis_module == PLACEHOLDER:
    period_type = st.selectbox("3. 选择统计周期", [PLACEHOLDER, "按月统计", "按周统计"], index=0)
    today = date.today()
    month_start = today.replace(day=1)
    date_range = st.date_input("4. 选择时间范围", value=(month_start, today), format="YYYY-MM-DD")

st.caption(
    "说明：货量=ETA；提柜=实际抵仓时间；拆柜=拆柜完成时间。"
    "产品类型筛选已移除，所有功能统一按上传文件中的全部有效数据处理。"
    "派送原数据处理=全量出库数据清洗，不做时间筛选；只负责合并、剔除无效批次、识别FTL/LTL、FTL按车次号合并、识别FBA/FBX与邮编。"
    "同一FTL车次混多个目的地时，目的地识别字段按该车次内出库体积最大的明细行覆盖。"
    "派送数据匹配及分析支持两种输入：一是派送一结果+一个或多个鲲运匹配列表；二是上一次派送二报告，在邮编异常审核表中补充邮编后直接重新上传。"
    "6B支持多文件上传；结构完全相同的匹配文件默认纵向合并，结构不同的文件按字段并集合并并保留来源文件名。"
    "邮编异常审核表请填写“补充标准邮编”，可选填写“补充目的州”。工具会把补入邮编的数据合并回分析主表重新计算。"
    "干线识别优先读车次/批次备注中的 NJ / SAV / DAL，再读调拨目标仓和邮编规则。干线只对LA仓派送分析生效。"
    "LTL不计入发车数，只参与方数结构；邮编列按文本处理；四位邮编自动补0。FTL车型缺失默认53尺大车；同车次装车类型同时出现卡板和地板时，聚合后按地板。"
)


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
    """6B匹配文件多文件合并：同结构直接纵向合并；不同结构按字段并集合并。"""
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


selection_complete = warehouse != PLACEHOLDER and analysis_module != PLACEHOLDER
if analysis_module in NORMAL_MODULES or analysis_module == PLACEHOLDER:
    selection_complete = selection_complete and period_type != PLACEHOLDER
elif analysis_module == DELIVERY_STAGE2_MODULE:
    selection_complete = selection_complete and period_type != PLACEHOLDER

if analysis_module == DELIVERY_STAGE1_MODULE:
    raw_files = st.file_uploader("3. 上传派送原始数据文件（可多选，格式需一致）", type=["xlsx", "xls"], accept_multiple_files=True)
    if raw_files:
        st.success(f"已上传 {len(raw_files)} 个文件。")
    if st.button("开始处理派送原数据", type="primary"):
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
                st.subheader("清洗后数据预览")
                st.dataframe(cleaned_batches.head(100), use_container_width=True)
                st.subheader("邮编异常数据预览")
                st.dataframe(zip_audit_df.head(100), use_container_width=True)
                st.subheader("无效数据预览")
                st.dataframe(invalid_detail.head(100), use_container_width=True)
                output = tool_common.write_sheets_to_excel({"清洗后数据": cleaned_batches, "邮编异常数据": zip_audit_df, "无效数据": invalid_detail})
                file_name = tool_common.build_output_filename(warehouse, DELIVERY_STAGE1_MODULE)
                st.download_button("下载派送一结果 Excel", output, file_name, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        except Exception as e:
            st.error("派送原数据处理失败，请检查文件字段、仓点选择或源数据格式。")
            st.exception(e)

elif analysis_module == DELIVERY_STAGE2_MODULE:
    stage1_file = st.file_uploader("4A. 上传派送一结果，或上传已补充邮编异常审核的派送二报告", type=["xlsx", "xls"], key="stage1_result_file")
    match_files = st.file_uploader(
        "4B. 上传人工匹配文件（可选，可多选；鲲运导出列表也可，需含批次号 + 邮编/目的地邮编/标准邮编，可含省/州）",
        type=["xlsx", "xls"],
        accept_multiple_files=True,
        key="manual_match_file",
    )
    if match_files:
        st.success(f"4B已上传 {len(match_files)} 个匹配文件。结构相同会自动按行合并。")

    if st.button("开始匹配并生成派送分析报告", type="primary"):
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
                metrics = delivery_workflow.process_stage2_analysis(cleaned_batches, match_df, period_type=period_type)
                st.success("派送分析报告已生成，详细结果请下载Excel查看。")
                st.write("报告结构：货量、FBA货量排行、FBX平台仓货量、发车量、派送时效、成本。")
                output = tool_common.write_sheets_to_excel(metrics)
                file_name = tool_common.build_output_filename(warehouse, DELIVERY_STAGE2_MODULE, period_type)
                st.download_button("下载派送分析报告 Excel", output, file_name, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        except Exception as e:
            st.error("派送数据匹配及分析失败，请检查派送一结果文件、匹配文件字段或批次号是否一致。")
            st.exception(e)

else:
    uploaded_file = st.file_uploader("5. 上传 Excel", type=["xlsx", "xls"])
    if uploaded_file is not None:
        try:
            uploaded_file.seek(0)
            excel_file = pd.ExcelFile(uploaded_file)
            sheet_names = excel_file.sheet_names
            sheet_name = st.selectbox("选择工作表", sheet_names) if len(sheet_names) > 1 else sheet_names[0]
            st.success(f"文件上传成功，当前工作表：{sheet_name}")
            if st.button("开始分析", type="primary"):
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
                    st.download_button("下载结果 Excel", output, file_name, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        except Exception as e:
            st.error("处理失败，请检查文件字段、工作表、时间范围或分析模块是否匹配。")
            st.exception(e)
    else:
        st.info("请先完成选择，并上传 Excel 文件。")

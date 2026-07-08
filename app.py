import streamlit as st
import pandas as pd
from io import BytesIO
from datetime import date, datetime

from processors import process_uploaded_file


st.set_page_config(
    page_title="美盈产品数据处理工具",
    layout="wide"
)

st.title("美盈产品数据处理工具")

st.write("请选择分析维度、时间范围并上传对应 Excel，系统将自动清洗、筛选、汇总并生成结果文件。")

st.divider()

PLACEHOLDER = "请填入"

warehouse = st.selectbox(
    "1. 选择仓点",
    [PLACEHOLDER, "LA", "NJ", "SAV", "DAL", "四仓合并"],
    index=0
)

product_type = st.selectbox(
    "2. 选择产品类型",
    [PLACEHOLDER, "FBA", "FBX", "全部"],
    index=0
)

analysis_module = st.selectbox(
    "3. 选择分析模块",
    [
        PLACEHOLDER,
        "FBA仓点货量排行",
        "FBX平台仓点货量分析",
        "派送时效分析",
        "提柜时效分析",
        "拆柜时效分析"
    ],
    index=0
)

period_type = st.selectbox(
    "4. 选择统计周期",
    [PLACEHOLDER, "按月统计", "按周统计"],
    index=0
)

today = date.today()
month_start = today.replace(day=1)

date_range = st.date_input(
    "5. 选择时间范围",
    value=(month_start, today),
    format="YYYY-MM-DD"
)

uploaded_file = st.file_uploader(
    "6. 上传 Excel",
    type=["xlsx", "xls"]
)

st.caption(
    "说明：网页工具会按所选时间范围筛选数据，再按月或按周汇总。"
    "各模块使用的筛选日期字段为：FBA/FBX/派送=出库时间；提柜=提柜时间；拆柜=拆柜完成时间。"
)


def selected_date_range_is_valid(value):
    return isinstance(value, (tuple, list)) and len(value) == 2 and value[0] is not None and value[1] is not None


selection_complete = all(
    item != PLACEHOLDER
    for item in [warehouse, product_type, analysis_module, period_type]
)

date_range_valid = selected_date_range_is_valid(date_range)

if uploaded_file is not None:
    try:
        uploaded_file.seek(0)
        excel_file = pd.ExcelFile(uploaded_file)
        sheet_names = excel_file.sheet_names

        if len(sheet_names) > 1:
            sheet_name = st.selectbox("选择工作表", sheet_names)
        else:
            sheet_name = sheet_names[0]

        st.success(f"文件上传成功，当前工作表：{sheet_name}")

        if st.button("开始分析", type="primary"):
            if not selection_complete:
                st.warning("请先把仓点、产品类型、分析模块、统计周期都选择完整。")
            elif not date_range_valid:
                st.warning("请选择完整的开始日期和结束日期。")
            elif date_range[0] > date_range[1]:
                st.warning("开始日期不能晚于结束日期。")
            else:
                uploaded_file.seek(0)

                detail_df, result_df, final_module = process_uploaded_file(
                    uploaded_file=uploaded_file,
                    sheet_name=sheet_name,
                    warehouse=warehouse,
                    product_type=product_type,
                    analysis_module=analysis_module,
                    period_type=period_type,
                    start_date=date_range[0],
                    end_date=date_range[1]
                )

                st.subheader("数据处理结果")
                st.dataframe(result_df, use_container_width=True)

                st.subheader("清洗后的数据集预览")
                st.dataframe(detail_df.head(100), use_container_width=True)

                output = BytesIO()

                with pd.ExcelWriter(output, engine="openpyxl") as writer:
                    detail_df.to_excel(writer, index=False, sheet_name="清洗后的数据集")
                    result_df.to_excel(writer, index=False, sheet_name="数据处理结果")

                output.seek(0)

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                start_date_text = date_range[0].strftime("%Y%m%d")
                end_date_text = date_range[1].strftime("%Y%m%d")
                file_name = (
                    f"{final_module}_{warehouse}_{product_type}_{period_type}_"
                    f"{start_date_text}-{end_date_text}_{timestamp}.xlsx"
                )

                st.download_button(
                    label="下载结果 Excel",
                    data=output,
                    file_name=file_name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

    except Exception as e:
        st.error("处理失败，请检查文件字段、工作表、时间范围或分析模块是否匹配。")
        st.exception(e)
else:
    st.info("请先完成选择，并上传 Excel 文件。")

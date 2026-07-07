import streamlit as st
import pandas as pd
from io import BytesIO
from datetime import datetime

from processors import process_uploaded_file


st.set_page_config(
    page_title="美盈产品数据处理工具",
    layout="wide"
)

st.title("美盈产品数据处理工具")

st.write("请选择分析维度并上传对应 Excel，系统将自动清洗、汇总并生成结果文件。")

st.divider()

warehouse = st.selectbox(
    "1. 选择仓点",
    ["LA", "NJ", "SAV", "DAL", "四仓合并"]
)

product_type = st.selectbox(
    "2. 选择产品类型",
    ["FBA", "FBX", "全部"]
)

analysis_module = st.selectbox(
    "3. 选择分析模块",
    [
        "FBA仓点货量排行",
        "FBX平台仓点货量分析",
        "派送时效分析",
        "提柜时效分析",
        "拆柜时效分析"
    ]
)

period_type = st.selectbox(
    "4. 选择统计周期",
    ["按月统计", "按周统计"]
)

uploaded_file = st.file_uploader(
    "5. 上传 Excel",
    type=["xlsx", "xls"]
)

st.caption("说明：网页工具不筛选具体日期范围。请先在原系统导出所需时间范围的数据，本工具只负责按月或按周汇总分析。")

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
            uploaded_file.seek(0)

            detail_df, result_df, final_module = process_uploaded_file(
                uploaded_file=uploaded_file,
                sheet_name=sheet_name,
                warehouse=warehouse,
                product_type=product_type,
                analysis_module=analysis_module,
                period_type=period_type
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
            file_name = f"{final_module}_{warehouse}_{product_type}_{period_type}_{timestamp}.xlsx"

            st.download_button(
                label="下载结果 Excel",
                data=output,
                file_name=file_name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

    except Exception as e:
        st.error("处理失败，请检查文件字段、工作表或分析模块是否匹配。")
        st.exception(e)
else:
    st.info("请先完成选择，并上传 Excel 文件。")

import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
from datetime import datetime, date
import base64
import requests

# 1. 頁面基礎設定
st.set_page_config(
    page_title="兒童發展基金 (CDF) 服務平台",
    page_icon="🌱",
    layout="centered",
    initial_sidebar_state="collapsed"
)

# 試算表網址與 Google Apps Script 上傳端點
SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/1cQlXUXPPjI7J3svQ0mVSuIKqvafkZ5WUaEqCU1_MfDs/edit"
GAS_UPLOAD_URL = "https://script.google.com/macros/s/AKfycbxBoon8E4PDr5k_xEfCY0VKYakWYizHSZe02_wOQ0RgOAoIoRIbbVe01q8fQzr7RDeF1A/exec"

# 青瓷綠 CSS 風格
st.markdown("""
<style>
    .main-title { font-size: 22px; font-weight: 700; color: #21867a; text-align: center; margin-bottom: 2px; }
    .sub-title { font-size: 13px; color: #666; text-align: center; margin-bottom: 15px; }
    .news-card {
        background-color: #ffffff;
        border: 1px solid #d0e4e4;
        border-radius: 8px;
        padding: 12px 14px;
        margin-bottom: 12px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.02);
    }
    .badge-pinned { background: #e05252; color: white; padding: 2px 6px; border-radius: 4px; font-size: 11px; }
    .badge-tag { background: #eef4f4; color: #21867a; padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; }
    .badge-cat { background: #e9a825; color: white; padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; margin-left: 4px; }
    .activity-card {
        background-color: #f7faf9;
        border: 1px solid #d0e4e4;
        border-radius: 8px;
        padding: 12px 14px;
        margin-bottom: 12px;
    }
    .privacy-notice {
        background-color: #eef4f4;
        border-left: 4px solid #2a9d8f;
        padding: 8px 12px;
        font-size: 12px;
        color: #333;
        margin-bottom: 15px;
    }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="main-title">兒童發展基金 (CDF)</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-title">浸信會愛羣社會服務處 · 家長及學童專屬平台</div>', unsafe_allow_html=True)

# 2. 連接 Google Sheet
conn = st.connection("gsheets", type=GSheetsConnection)

# 上傳入數紙到 Google Drive 輔助函數 (透過 Google Apps Script 中轉)
def upload_receipt_to_drive(uploaded_file, student_id, year_month):
    try:
        file_ext = uploaded_file.name.split('.')[-1]
        custom_filename = f"DEP_{year_month}_{student_id.replace('/', '_')}_{int(datetime.now().timestamp())}.{file_ext}"

        base64_data = base64.b64encode(uploaded_file.getvalue()).decode('utf-8')

        payload = {
            "filename": custom_filename,
            "mimeType": uploaded_file.type or "image/jpeg",
            "base64": base64_data
        }

        res = requests.post(GAS_UPLOAD_URL, json=payload, timeout=60)

        # 嘗試解析 JSON；若回傳 HTML 則顯示前 300 字元方便排查
        try:
            res_json = res.json()
        except Exception:
            st.error(f"❌ Google Apps Script 未回傳 JSON（HTTP {res.status_code}）。內容片段：\n{res.text[:300]}")
            return f"[上傳失敗: {uploaded_file.name}]"

        if res_json.get("status") == "success":
            return res_json.get("url")
        else:
            err_msg = res_json.get("message", "未知錯誤")
            st.error(f"Google 雲端寫入失敗: {err_msg}")
            return f"[上傳失敗: {uploaded_file.name}]"
    except Exception as e:
        st.error(f"上傳圖片時發生連線錯誤: {e}")
        return f"[圖片已提交（上傳雲端失敗）: {uploaded_file.name}]"

# 3. 三大分頁
tab_news, tab_activities, tab_savings = st.tabs(["📢 最新消息", "🎯 活動報名", "💰 每月存款"])

# ==================== 分頁一：最新消息 ====================
with tab_news:
    st.write("#### 📢 計劃消息及提示")
    try:
        news_df = conn.read(worksheet="News", ttl="3s")
        if news_df is None or news_df.dropna(how="all").empty:
            st.info("目前未有最新消息發布。")
        else:
            news_df = news_df.dropna(how="all")
            if "是否置頂(Is_Pinned)" in news_df.columns:
                news_df["是否置頂(Is_Pinned)"] = news_df["是否置頂(Is_Pinned)"].fillna("").astype(str).str.strip().str.upper().isin(["TRUE", "1", "YES", "是"])
                news_df = news_df.sort_values(by=["是否置頂(Is_Pinned)", "日期(Date)"], ascending=[False, False])
            
            for _, item in news_df.iterrows():
                is_pinned = item.get("是否置頂(Is_Pinned)", False)
                pin_tag = '<span class="badge-pinned">置頂重要</span> ' if is_pinned else ''
                category = item.get('消息類別(Category)', '提示')
                
                st.markdown(f"""
                <div class="news-card">
                    <div style="font-size: 11px; color: #888; margin-bottom: 4px;">
                        {pin_tag}<span class="badge-tag">{category}</span> · {item.get('日期(Date)', '')}
                    </div>
                    <div style="font-weight: 700; color: #21867a; font-size: 15px; margin-bottom: 6px;">
                        {item.get('標題(Title)', '')}
                    </div>
                    <div style="font-size: 13px; color: #444; line-height: 1.5; white-space: pre-wrap;">
                        {item.get('詳細內容(Content)', '')}
                    </div>
                </div>
                """, unsafe_allow_html=True)
    except Exception as e:
        st.warning(f"暫時未能載入消息 (錯誤詳情: {e})")

# ==================== 分頁二：活動報名 ====================
with tab_activities:
    st.write("#### 🎯 活動報名")
    st.markdown("""
    <div class="privacy-notice">
        🔒 <b>私隱保障</b>：本平台不公開任何參加者姓名及電話，請憑「計劃編號」登記。
    </div>
    """, unsafe_allow_html=True)

    try:
        activities_df = conn.read(worksheet="Activities", ttl="3s")
        if activities_df is not None:
            activities_df = activities_df.dropna(how="all")
        else:
            activities_df = pd.DataFrame()

        status_col = "報名狀況(Status)"
        if status_col in activities_df.columns:
            open_acts = activities_df[activities_df[status_col].astype(str).str.strip().isin(["接受報名", "開放報名", "開放中"])]
        else:
            open_acts = pd.DataFrame()
        
        if open_acts.empty:
            st.info("目前暫未有開放報名的活動。")
        else:
            act_dict = {}
            for _, act in open_acts.iterrows():
                act_id = act.get('活動類別(Activity_ID)', '')
                act_title = act.get('活動名稱(Title)', '')
                cat_id = act.get('主題類別(Cat_ID)', '')
                cat_badge = f'<span class="badge-cat">{cat_id}</span>' if pd.notna(cat_id) and str(cat_id).strip() else ''
                
                label = f"{act_title} ({act.get('活動日期及時間(Date_Time)', '')})"
                act_dict[label] = (act_id, act_title)
                
                st.markdown(f"""
                <div class="activity-card">
                    <span class="badge-tag">{act.get(status_col, '接受報名')}</span>{cat_badge}
                    <div style="font-weight: 700; color: #21867a; margin-top: 4px; font-size: 16px;">{act_title}</div>
                    <div style="font-size: 12px; color: #555; line-height: 1.6; margin-top: 4px;">
                        👥 <b>類別/對象</b>：{act_id} / {act.get('對象(Target)', '')}<br>
                        📅 <b>時間</b>：{act.get('活動日期及時間(Date_Time)', '')}<br>
                        📍 <b>地點</b>：{act.get('活動地點(Venue)', '')} | 👥 <b>名額</b>：{act.get('名額(Quota)', '')} 人<br>
                        📝 <b>詳情</b>：{act.get('活動詳情(Details)', '無')}
                    </div>
                </div>
                """, unsafe_allow_html=True)
            
            st.write("---")
            with st.form("reg_form", clear_on_submit=True):
                st.write("##### 填寫活動報名")
                chosen_act = st.selectbox("1. 選擇報名活動 *", list(act_dict.keys()))
                reg_district = st.selectbox("2. 所屬區域 *", ["深水埗區", "九龍城及油尖旺區", "觀塘區"])
                reg_stu_id = st.text_input("3. 計劃編號 *", placeholder="例：001").strip().upper()
                reg_phone4 = st.text_input("4. 登記電話最後 4 個字（身分核對）*", placeholder="例：1234", max_chars=4).strip()
                reg_headcount = st.selectbox("5. 參加人數 *", ["1人 (學童)", "2人 (學童及家長)"])
                
                if st.form_submit_button("確認提交報名 🚀", use_container_width=True):
                    if not reg_stu_id:
                        st.error("請輸入計劃編號！")
                    elif len(reg_phone4) != 4 or not reg_phone4.isdigit():
                        st.error("電話最後 4 個字必須為 4 位數字！")
                    else:
                        act_id, act_title = act_dict[chosen_act]
                        
                        reg_data = conn.read(worksheet="Registrations", ttl=0)
                        if reg_data is not None:
                            reg_data = reg_data.dropna(how="all")
                        else:
                            reg_data = pd.DataFrame()

                        # 自動匹配試算表中的區域欄位名稱，若無則預設為 "所屬區域(District)"
                        district_reg_col = [c for c in reg_data.columns if ("區" in c or "District" in c)][0] if (not reg_data.empty and any(("區" in c or "District" in c) for c in reg_data.columns)) else "所屬區域(District)"

                        new_reg = pd.DataFrame([{
                            "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            district_reg_col: reg_district,
                            "活動類別(Activity_ID)": act_id,
                            "活動名稱(Activity_Title)": act_title,
                            "計劃編號(Participant_ID)": reg_stu_id,
                            "登記電話最後4個字(Phone_Last4)": reg_phone4,
                            "參加人數(Headcount)": reg_headcount
                        }])

                        updated_reg = pd.concat([reg_data, new_reg], ignore_index=True)
                        conn.update(worksheet="Registrations", data=updated_reg)
                        st.cache_data.clear()
                        st.success(f"🎉 報名成功！已記錄計劃編號：{reg_stu_id}（{reg_district}）")
    except Exception as e:
        st.warning(f"暫時未能載入活動 (錯誤詳情: {e})")

# ==================== 分頁三：每月存款 ====================
with tab_savings:
    st.write("#### 💰 每月 $200 目標儲蓄供款上傳")
    st.markdown("""
    <div class="privacy-notice">
        ℹ️ 請家長在完成存款後，<b>將入數紙拍照在此上傳</b>。同工於每月月結單出具後將核對並入帳。
    </div>
    """, unsafe_allow_html=True)

    with st.form("savings_form", clear_on_submit=True):
        sav_district = st.selectbox("1. 所屬區份 *", ["深水埗 (SSP)", "九龍城及油尖旺 (KCY)", "觀塘 (KTG)"])
        sav_student_id = st.text_input("2. 學生編號 *", placeholder="例：CDF11/SSP/001").strip().upper()
        
        today = date.today()
        month_options = [
            f"{today.year}年{today.month}月",
            f"{today.year if today.month > 1 else today.year - 1}年{today.month - 1 if today.month > 1 else 12}月"
        ]
        sav_month = st.selectbox("3. 存款月份 *", month_options)
        sav_amount = st.number_input("4. 存款金額 (HK$) *", min_value=100.0, max_value=1000.0, value=200.0, step=50.0)
        sav_depositor = st.text_input("5. 存款人姓名／關係 *", placeholder="例：陳大文 (父親)").strip()
        
        col1, col2 = st.columns(2)
        with col1:
            sav_date = st.date_input("6. 存款日期 *", value=today)
        with col2:
            sav_time = st.time_input("7. 存款時間 *", value=datetime.now().time())

        sav_file = st.file_uploader("8. 上傳入數紙／網銀截圖相片 *", type=["jpg", "jpeg", "png", "pdf"])

        if st.form_submit_button("確認上傳存款憑證 📤", use_container_width=True):
            if not sav_student_id:
                st.error("請填寫學生編號！")
            elif not sav_depositor:
                st.error("請填寫存款人姓名或關係！")
            elif sav_file is None:
                st.error("請拍攝並上傳入數紙相片！")
            else:
                with st.spinner("正在上傳憑證並寫入紀錄..."):
                    receipt_link = upload_receipt_to_drive(sav_file, sav_student_id, sav_month)
                    
                    sav_data = conn.read(worksheet="Savings", ttl=0)
                    if sav_data is not None:
                        sav_data = sav_data.dropna(how="all")
                    else:
                        sav_data = pd.DataFrame()

                    district_col = [c for c in sav_data.columns if "所屬區份" in c][0] if (not sav_data.empty and any("所屬區份" in c for c in sav_data.columns)) else "所屬區份(District)"

                    new_savings = pd.DataFrame([{
                        "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        district_col: sav_district,
                        "學生編號(Student_ID)": sav_student_id,
                        "存款月份(Year_Month)": sav_month,
                        "存款金額(Amount)": sav_amount,
                        "存款人姓名(Depositor_Name)": sav_depositor,
                        "存款日期(Deposit_Date)": sav_date.strftime("%Y-%m-%d"),
                        "存款時間(Deposit_Time)": sav_time.strftime("%H:%M"),
                        "上傳連結(Receipt_URL)": receipt_link,
                        "審核狀態(Status)": "待核對"
                    }])

                    try:
                        updated_savings = pd.concat([sav_data, new_savings], ignore_index=True)
                        conn.update(worksheet="Savings", data=updated_savings)
                        st.cache_data.clear()
                        st.success("✅ 存款憑證已成功提交！")
                        st.info(f"已記錄學生編號：**{sav_student_id}**（{sav_district}，{sav_month}，HK$ {sav_amount:.2f}）")
                    except Exception as e:
                        st.error(f"提交失敗，請稍後重試 (錯誤詳情: {e})")

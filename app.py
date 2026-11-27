import base64
import html
import io
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd
import requests
import streamlit as st
from PIL import Image, ImageOps, UnidentifiedImageError
from streamlit_gsheets import GSheetsConnection

# =========================================================
# 1. 頁面設定與常數
# =========================================================
st.set_page_config(
    page_title="兒童發展基金 (CDF) 服務平台",
    page_icon="🌱",
    layout="centered",
    initial_sidebar_state="collapsed",
)

try:
    HKT = ZoneInfo("Asia/Hong_Kong")
except ZoneInfoNotFoundError:
    HKT = timezone(timedelta(hours=8))

# 統一全平台分區名稱與 Google Drive 資料夾代碼
DISTRICT_MAP = {
    "深水埗區 (SSP)": "SSP",
    "九龍城及油尖旺區 (KCY)": "KCY",
    "觀塘區 (KTG)": "KTG",
}
DISTRICT_OPTIONS = list(DISTRICT_MAP.keys())

OPEN_ACTIVITY_STATUSES = {"接受報名", "開放報名", "開放中"}
HEADCOUNT_OPTIONS = ["1人 (學童)", "2人 (學童及家長)"]

MAX_UPLOAD_MB = 10
IMAGE_MAX_DIM = 1600
IMAGE_JPEG_QUALITY = 80
REQUEST_TIMEOUT_SEC = 60

# 若設為 True，查詢存款紀錄時會嚴格要求舊紀錄也必須有相符的電話尾 4 位；
# 預設 False：新版紀錄嚴格核對電話尾 4 位，舊版未有電話欄位的歷史紀錄仍可安全查詢（不顯示姓名與相片）
STRICT_SAVINGS_PHONE_CHECK = False

# 讀取 Secrets（不再硬編碼正式網址，避免公開倉庫洩漏）
GAS_WEBAPP_URL = st.secrets.get("GAS_WEBAPP_URL") or st.secrets.get("GAS_UPLOAD_URL")
GAS_API_TOKEN = st.secrets.get("GAS_API_TOKEN")

# =========================================================
# 2. 青瓷綠介面樣式
# =========================================================
st.markdown(
    """
<style>
    .main-title { font-size: 22px; font-weight: 700; color: #21867a; text-align: center; margin-bottom: 2px; }
    .sub-title { font-size: 13px; color: #666; text-align: center; margin-bottom: 15px; }
    .news-card {
        background-color: #ffffff;
        border: 1px solid #d0e4e4;
        border-radius: 8px;
        padding: 12px 14px;
        margin-bottom: 12px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.03);
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
""",
    unsafe_allow_html=True,
)

st.markdown('<div class="main-title">兒童發展基金 (CDF)</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub-title">浸信會愛羣社會服務處 · 家長、學童及友師專屬平台</div>',
    unsafe_allow_html=True,
)

if not GAS_WEBAPP_URL or not GAS_API_TOKEN:
    st.error(
        "系統尚未完成設定：請於 `.streamlit/secrets.toml` 設定 `GAS_WEBAPP_URL` 及 `GAS_API_TOKEN`。"
    )
    st.stop()


# =========================================================
# 3. 共用工具與資料標準化函數
# =========================================================
def now_hkt() -> datetime:
    """取得香港時間（UTC+8）。"""
    return datetime.now(HKT)


def safe_text(val, default: str = "") -> str:
    """安全轉換儲存格值：處理 NaN 並進行 HTML 轉義，防止版面損毀或 XSS。"""
    if val is None or pd.isna(val):
        return default
    text = str(val).strip()
    return html.escape(text) if text else default


def format_quota(val, default: str = "待定") -> str:
    """把 pandas 讀入的浮點數名額（如 30.0）美化為整數文字。"""
    if val is None or pd.isna(val):
        return default
    if isinstance(val, float) and val.is_integer():
        return str(int(val))
    text = str(val).strip()
    return html.escape(text) if text else default


def normalize_district(val) -> str:
    """將各類分區寫法統一為 SSP / KCY / KTG（兼容新舊資料）。"""
    if val is None or pd.isna(val):
        return ""
    s = str(val).strip().upper()
    if "SSP" in s or "深水埗" in s:
        return "SSP"
    if "KCY" in s or "九龍城" in s or "油尖旺" in s:
        return "KCY"
    if "KTG" in s or "觀塘" in s:
        return "KTG"
    return s


def normalize_id_last3(val) -> str:
    """將編號統一為最後 3 位補零數字（解決試算表去掉開頭 0 或變 .0 的問題）。"""
    if val is None or pd.isna(val):
        return ""
    s = str(val).strip()
    if s.endswith(".0"):
        s = s[:-2]
    digits = re.findall(r"\d+", s)
    if digits:
        return digits[-1][-3:].zfill(3)
    return s.upper()


def normalize_phone4(val) -> str:
    """將電話尾數統一為 4 位補零數字。"""
    if val is None or pd.isna(val):
        return ""
    s = str(val).strip()
    if s.endswith(".0"):
        s = s[:-2]
    digits = re.sub(r"\D", "", s)
    return digits[-4:].zfill(4) if digits else ""


def is_valid_3digit_id(val: str) -> bool:
    return bool(re.fullmatch(r"\d{3}", val))


def is_valid_phone4(val: str) -> bool:
    return bool(re.fullmatch(r"\d{4}", val))


def get_recent_months(count: int) -> list[str]:
    """以香港時間產生最近 count 個月份選項（由當月起倒數）。"""
    today = now_hkt().date()
    y, m = today.year, today.month
    months: list[str] = []
    for _ in range(count):
        months.append(f"{y}年{m}月")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return months


def init_form_state(prefix: str) -> int:
    """維護表單版本號，只在真正提交成功時遞增以清空表單。"""
    ver_key = f"{prefix}_form_ver"
    if ver_key not in st.session_state:
        st.session_state[ver_key] = 0
    return st.session_state[ver_key]


def show_and_clear_flash(prefix: str) -> None:
    """於表單成功重置後顯示一次性成功訊息。"""
    msg_key = f"{prefix}_flash_success"
    detail_key = f"{prefix}_flash_detail"
    if msg_key in st.session_state:
        st.success(st.session_state.pop(msg_key))
    if detail_key in st.session_state:
        st.info(st.session_state.pop(detail_key))


def complete_form_submission(prefix: str, success_msg: str, detail_msg: str | None = None) -> None:
    """儲存成功訊息、遞增表單版本號並重新整理頁面以清空欄位。"""
    st.session_state[f"{prefix}_flash_success"] = success_msg
    if detail_msg:
        st.session_state[f"{prefix}_flash_detail"] = detail_msg
    st.session_state[f"{prefix}_form_ver"] = st.session_state.get(f"{prefix}_form_ver", 0) + 1
    st.session_state.pop(f"{prefix}_default_time", None)
    st.rerun()


def compress_upload_if_image(uploaded_file) -> tuple[bool, tuple[bytes, str, str] | str]:
    """檢查檔案大小並壓縮圖片以加快手機上傳速度；PDF 則保持原樣。"""
    if uploaded_file.size > MAX_UPLOAD_MB * 1024 * 1024:
        return False, f"檔案大小超過 {MAX_UPLOAD_MB}MB 上限，請重新拍攝或壓縮後再試。"

    raw_bytes = uploaded_file.getvalue()
    ext = Path(uploaded_file.name).suffix.lower().lstrip(".") or "bin"
    mime = uploaded_file.type or "application/octet-stream"

    if ext in {"jpg", "jpeg", "png"}:
        try:
            with Image.open(io.BytesIO(raw_bytes)) as img:
                img = ImageOps.exif_transpose(img)
                if img.mode in ("RGBA", "P", "LA"):
                    img = img.convert("RGB")
                img.thumbnail((IMAGE_MAX_DIM, IMAGE_MAX_DIM))
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=IMAGE_JPEG_QUALITY, optimize=True)
                return True, (buf.getvalue(), "jpg", "image/jpeg")
        except (UnidentifiedImageError, OSError):
            return False, "無法讀取所上傳的圖片，請確認檔案未損毀並重新選擇。"

    return True, (raw_bytes, ext, mime)


def call_gas_api(payload: dict) -> tuple[bool, dict | str]:
    """統一呼叫 Google Apps Script API，帶入認證 Token 與錯誤處理。"""
    body = {"token": GAS_API_TOKEN, **payload}
    try:
        res = requests.post(GAS_WEBAPP_URL, json=body, timeout=REQUEST_TIMEOUT_SEC)
    except requests.Timeout:
        return False, "連線逾時，請檢查網絡後重試。"
    except requests.RequestException as e:
        return False, f"無法連接伺服器：{e}"

    try:
        res_json = res.json()
    except ValueError:
        snippet = res.text[:200].strip()
        return False, f"伺服器未回傳有效 JSON（HTTP {res.status_code}）：{snippet}"

    if res.status_code >= 400 or res_json.get("status") != "success":
        return False, res_json.get("message", f"伺服器回傳錯誤（HTTP {res.status_code}）")

    return True, res_json


# =========================================================
# 4. Google Sheets 唯讀資料載入（獨立快取，避免撞 API 限額）
# =========================================================
conn = st.connection("gsheets", type=GSheetsConnection)


@st.cache_data(ttl=300, show_spinner=False)
def load_news() -> pd.DataFrame:
    df = conn.read(worksheet="News", ttl=0)
    return df.dropna(how="all").copy() if df is not None else pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def load_activities() -> pd.DataFrame:
    df = conn.read(worksheet="Activities", ttl=0)
    return df.dropna(how="all").copy() if df is not None else pd.DataFrame()


@st.cache_data(ttl=30, show_spinner=False)
def load_worksheet_for_lookup(worksheet: str) -> pd.DataFrame:
    df = conn.read(worksheet=worksheet, ttl=0)
    return df.dropna(how="all").copy() if df is not None else pd.DataFrame()


# =========================================================
# 5. 五大分頁
# =========================================================
tab_news, tab_activities, tab_savings, tab_mentors, tab_lookup = st.tabs(
    ["📢 最新消息", "🎯 活動報名", "💰 每月存款", "🤝 友師記錄", "🔍 紀錄查詢"]
)

# ==================== 分頁一：最新消息 ====================
with tab_news:
    header_col, btn_col = st.columns([4, 1])
    with header_col:
        st.write("#### 📢 計劃消息及提示")
    with btn_col:
        if st.button("🔄 更新", key="refresh_news", use_container_width=True):
            load_news.clear()
            st.rerun()

    try:
        news_df = load_news().copy()
        if news_df.empty:
            st.info("目前未有最新消息發布。")
        else:
            pin_col = "是否置頂(Is_Pinned)"
            date_col = "日期(Date)"

            if pin_col in news_df.columns:
                news_df["_is_pinned"] = (
                    news_df[pin_col]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                    .str.upper()
                    .isin(["TRUE", "1", "YES", "是"])
                )
            else:
                news_df["_is_pinned"] = False

            if date_col in news_df.columns:
                news_df["_sort_date"] = pd.to_datetime(news_df[date_col], errors="coerce")
            else:
                news_df["_sort_date"] = pd.NaT

            news_df = news_df.sort_values(
                by=["_is_pinned", "_sort_date"],
                ascending=[False, False],
                na_position="last",
            )

            for _, item in news_df.iterrows():
                is_pinned = bool(item.get("_is_pinned", False))
                pin_tag = '<span class="badge-pinned">置頂重要</span> ' if is_pinned else ""
                category = safe_text(item.get("消息類別(Category)"), default="提示")
                date_str = safe_text(item.get(date_col))
                title = safe_text(item.get("標題(Title)"))
                content = safe_text(item.get("詳細內容(Content)"))

                st.markdown(
                    f"""
                <div class="news-card">
                    <div style="font-size: 11px; color: #888; margin-bottom: 4px;">
                        {pin_tag}<span class="badge-tag">{category}</span> · {date_str}
                    </div>
                    <div style="font-weight: 700; color: #21867a; font-size: 15px; margin-bottom: 6px;">
                        {title}
                    </div>
                    <div style="font-size: 13px; color: #444; line-height: 1.5; white-space: pre-wrap;">{content}</div>
                </div>
                """,
                    unsafe_allow_html=True,
                )
    except Exception as e:
        st.warning(f"暫時未能載入消息（錯誤詳情：{e}）")


# ==================== 分頁二：活動報名 ====================
with tab_activities:
    header_col, btn_col = st.columns([4, 1])
    with header_col:
        st.write("#### 🎯 活動報名")
    with btn_col:
        if st.button("🔄 更新", key="refresh_acts", use_container_width=True):
            load_activities.clear()
            st.rerun()

    st.markdown(
        """
    <div class="privacy-notice">
        🔒 <b>私隱保障</b>：本平台不公開任何參加者姓名及電話，請憑「計劃編號」及「電話最後 4 位數字」登記。
    </div>
    """,
        unsafe_allow_html=True,
    )

    show_and_clear_flash("reg")

    try:
        activities_df = load_activities()
        status_col = "報名狀況(Status)"

        if status_col in activities_df.columns:
            open_acts = activities_df[
                activities_df[status_col].astype(str).str.strip().isin(OPEN_ACTIVITY_STATUSES)
            ].reset_index(drop=True)
        else:
            open_acts = pd.DataFrame()

        if open_acts.empty:
            st.info("目前暫未有開放報名的活動。")
        else:
            act_options: dict[str, tuple[str, str]] = {}
            for idx, act in open_acts.iterrows():
                raw_act_id = "" if pd.isna(act.get("活動類別(Activity_ID)")) else str(act.get("活動類別(Activity_ID)")).strip()
                raw_act_title = "" if pd.isna(act.get("活動名稱(Title)")) else str(act.get("活動名稱(Title)")).strip()
                raw_date_time = "" if pd.isna(act.get("活動日期及時間(Date_Time)")) else str(act.get("活動日期及時間(Date_Time)")).strip()

                status_html = safe_text(act.get(status_col), default="接受報名")
                cat_html = safe_text(act.get("主題類別(Cat_ID)"))
                cat_badge = f'<span class="badge-cat">{cat_html}</span>' if cat_html else ""
                title_html = safe_text(raw_act_title)
                act_id_html = safe_text(raw_act_id)
                target_html = safe_text(act.get("對象(Target)"))
                dt_html = safe_text(raw_date_time)
                venue_html = safe_text(act.get("活動地點(Venue)"))
                quota_html = format_quota(act.get("名額(Quota)"))
                details_html = safe_text(act.get("活動詳情(Details)"), default="無")

                label = f"{idx + 1}. {raw_act_title} ({raw_date_time})"
                act_options[label] = (raw_act_id, raw_act_title)

                st.markdown(
                    f"""
                <div class="activity-card">
                    <span class="badge-tag">{status_html}</span>{cat_badge}
                    <div style="font-weight: 700; color: #21867a; margin-top: 4px; font-size: 16px;">{title_html}</div>
                    <div style="font-size: 12px; color: #555; line-height: 1.6; margin-top: 4px;">
                        👥 <b>類別／對象</b>：{act_id_html} / {target_html}<br>
                        📅 <b>時間</b>：{dt_html}<br>
                        📍 <b>地點</b>：{venue_html} | 👥 <b>名額</b>：{quota_html} 人<br>
                        📝 <b>詳情</b>：{details_html}
                    </div>
                </div>
                """,
                    unsafe_allow_html=True,
                )

            st.write("---")
            reg_ver = init_form_state("reg")
            with st.form(f"reg_form_{reg_ver}", clear_on_submit=False):
                st.write("##### 填寫活動報名")
                chosen_act = st.selectbox("1. 選擇報名活動 *", list(act_options.keys()))
                reg_district = st.selectbox("2. 所屬區域 *", DISTRICT_OPTIONS)
                reg_stu_id = st.text_input(
                    "3. 計劃編號（只需輸入最後 3 位數字）*",
                    placeholder="例：006、089、105",
                    max_chars=3,
                ).strip()
                reg_phone4 = st.text_input(
                    "4. 登記電話最後 4 個字（身分核對）*",
                    placeholder="例：1234",
                    max_chars=4,
                ).strip()
                reg_headcount = st.selectbox("5. 參加人數 *", HEADCOUNT_OPTIONS)

                submitted = st.form_submit_button("確認提交報名 🚀", use_container_width=True)

            if submitted:
                if not is_valid_3digit_id(reg_stu_id):
                    st.error("計劃編號必須為 3 位數字（例如：006、089）！")
                elif not is_valid_phone4(reg_phone4):
                    st.error("電話最後 4 個字必須為 4 位數字！")
                else:
                    act_id, act_title = act_options[chosen_act]
                    row_data = {
                        "Timestamp": now_hkt().strftime("%Y-%m-%d %H:%M:%S"),
                        "所屬區域(District)": reg_district,
                        "活動類別(Activity_ID)": act_id,
                        "活動名稱(Activity_Title)": act_title,
                        "計劃編號(Participant_ID)": reg_stu_id,
                        "登記電話最後4個字(Phone_Last4)": reg_phone4,
                        "參加人數(Headcount)": reg_headcount,
                    }

                    with st.spinner("正在提交報名紀錄..."):
                        ok, result = call_gas_api(
                            {
                                "action": "append_row",
                                "sheet": "Registrations",
                                "row": row_data,
                                "dedup_keys": [
                                    "所屬區域(District)",
                                    "活動名稱(Activity_Title)",
                                    "計劃編號(Participant_ID)",
                                    "登記電話最後4個字(Phone_Last4)",
                                ],
                            }
                        )

                    if ok:
                        load_worksheet_for_lookup.clear()
                        complete_form_submission(
                            "reg",
                            f"🎉 報名成功！已記錄計劃編號：{reg_stu_id}（{reg_district}）",
                            f"已報名活動：**{act_title}**（{reg_headcount}）",
                        )
                    else:
                        st.error(f"報名未能完成：{result}")
    except Exception as e:
        st.warning(f"暫時未能載入活動（錯誤詳情：{e}）")


# ==================== 分頁三：每月存款 ====================
with tab_savings:
    st.write("#### 💰 每月 $200 目標儲蓄供款上傳")
    st.markdown(
        """
    <div class="privacy-notice">
        ℹ️ 請家長在完成存款後，<b>將入數紙或網銀截圖在此上傳</b>。同工於每月月結單出具後將核對並入帳。
    </div>
    """,
        unsafe_allow_html=True,
    )

    show_and_clear_flash("sav")

    sav_ver = init_form_state("sav")
    if "sav_default_time" not in st.session_state:
        st.session_state["sav_default_time"] = now_hkt().time().replace(second=0, microsecond=0)

    sav_month_options = get_recent_months(2)
    today_hkt = now_hkt().date()

    with st.form(f"savings_form_{sav_ver}", clear_on_submit=False):
        sav_district = st.selectbox("1. 所屬區份 *", DISTRICT_OPTIONS)
        s_col1, s_col2 = st.columns(2)
        with s_col1:
            sav_student_id = st.text_input(
                "2. 學生編號（最後 3 位數字）*",
                placeholder="例：089",
                max_chars=3,
            ).strip()
        with s_col2:
            sav_phone4 = st.text_input(
                "3. 登記電話最後 4 個字（供查詢核對）*",
                placeholder="例：1234",
                max_chars=4,
            ).strip()

        sav_month = st.selectbox("4. 存款月份 *", sav_month_options)
        sav_amount = st.number_input(
            "5. 存款金額 (HK$) *",
            min_value=100.0,
            max_value=1000.0,
            value=200.0,
            step=50.0,
        )
        sav_depositor = st.text_input(
            "6. 入數人姓名 *",
            placeholder="例：陳大文",
        ).strip()

        col1, col2 = st.columns(2)
        with col1:
            sav_date = st.date_input("7. 存款日期 *", value=today_hkt, max_value=today_hkt)
        with col2:
            sav_time = st.time_input(
                "8. 存款時間 *",
                value=st.session_state["sav_default_time"],
                step=60,
            )

        sav_file = st.file_uploader(
            f"9. 上傳入數紙／網銀截圖相片（上限 {MAX_UPLOAD_MB}MB）*",
            type=["jpg", "jpeg", "png", "pdf"],
        )

        sav_submitted = st.form_submit_button("確認上傳存款憑證 📤", use_container_width=True)

    if sav_submitted:
        if not is_valid_3digit_id(sav_student_id):
            st.error("學生編號必須為 3 位數字（例如：089）！")
        elif not is_valid_phone4(sav_phone4):
            st.error("登記電話最後 4 個字必須為 4 位數字！")
        elif not sav_depositor:
            st.error("請填寫入數人姓名！")
        elif sav_file is None:
            st.error("請拍攝並上傳入數紙或網銀截圖！")
        else:
            with st.spinner("正在壓縮圖片、上傳憑證並寫入紀錄..."):
                prep_ok, prep_result = compress_upload_if_image(sav_file)
                if not prep_ok:
                    st.error(str(prep_result))
                else:
                    file_bytes, file_ext, mime_type = prep_result
                    district_code = normalize_district(sav_district) or "其他"
                    custom_filename = (
                        f"DEP_{sav_month}_{district_code}_{sav_student_id}_"
                        f"{int(now_hkt().timestamp())}.{file_ext}"
                    )

                    row_data = {
                        "Timestamp": now_hkt().strftime("%Y-%m-%d %H:%M:%S"),
                        "所屬區份(District)": sav_district,
                        "學生編號(Student_ID)": sav_student_id,
                        "登記電話最後4個字(Phone_Last4)": sav_phone4,
                        "存款月份(Year_Month)": sav_month,
                        "存款金額(Amount)": float(sav_amount),
                        "存款人姓名(Depositor_Name)": sav_depositor,
                        "存款日期(Deposit_Date)": sav_date.strftime("%Y-%m-%d"),
                        "存款時間(Deposit_Time)": sav_time.strftime("%H:%M"),
                        "審核狀態(Status)": "待核對",
                    }

                    ok, result = call_gas_api(
                        {
                            "action": "submit_savings",
                            "sheet": "Savings",
                            "file": {
                                "filename": custom_filename,
                                "mimeType": mime_type,
                                "base64": base64.b64encode(file_bytes).decode("utf-8"),
                                "district": district_code,
                                "year_month": sav_month,
                            },
                            "row": row_data,
                        }
                    )

                    if ok:
                        load_worksheet_for_lookup.clear()
                        complete_form_submission(
                            "sav",
                            "✅ 存款憑證已成功上傳並完成登記！",
                            f"已記錄學生編號：**{sav_student_id}**（{sav_district}，{sav_month}，HKD {sav_amount:.2f}）",
                        )
                    else:
                        st.error(f"❌ 上傳或提交失敗，資料尚未寫入，請稍後重試（詳情：{result}）")


# ==================== 分頁四：友師記錄 ====================
with tab_mentors:
    st.write("#### 🤝 友師每月接觸記錄")
    st.markdown(
        """
    <div class="privacy-notice">
        🔒 <b>私隱保障</b>：本表格不收集友師及學童之全名或完整電話，請憑「分區」、「學生編號」及「友師電話最後 4 位數字」核對記錄。
    </div>
    """,
        unsafe_allow_html=True,
    )

    show_and_clear_flash("mentor")

    mentor_ver = init_form_state("mentor")
    mentor_month_options = get_recent_months(3)

    with st.form(f"mentor_form_{mentor_ver}", clear_on_submit=False):
        st.write("##### 1. 基本資料")
        m_col1, m_col2 = st.columns(2)
        with m_col1:
            mentor_district = st.selectbox("所屬區域 *", DISTRICT_OPTIONS)
        with m_col2:
            mentor_month = st.selectbox("記錄月份 *", mentor_month_options)

        m_col3, m_col4 = st.columns(2)
        with m_col3:
            mentor_stu_id = st.text_input(
                "配對學生編號（只需輸入最後 3 位數字）*",
                placeholder="例：105",
                max_chars=3,
            ).strip()
        with m_col4:
            mentor_phone4 = st.text_input(
                "友師登記電話最後 4 個字 *",
                placeholder="例：1234",
                max_chars=4,
            ).strip()

        st.write("---")
        st.write("##### 2. 接觸情況及次數")

        no_contact = st.checkbox("⚠️ 本月份未有接觸學童", value=False)
        st.caption("請填寫該月份各方式的接觸次數（若本月有接觸，請填寫對應次數；若未有接觸，請保留 0）：")

        c1, c2 = st.columns(2)
        with c1:
            sms_count = st.number_input("📱 社交短訊（WhatsApp / Signal 等）次數", min_value=0, max_value=100, value=0, step=1)
            f2f_count = st.number_input("👥 面對面接觸次數", min_value=0, max_value=100, value=0, step=1)
        with c2:
            call_count = st.number_input("📞 電話通話次數", min_value=0, max_value=100, value=0, step=1)
            other_count = st.number_input("✨ 其他方式次數", min_value=0, max_value=100, value=0, step=1)

        other_details = st.text_input(
            "其他接觸方式說明（如填寫了「其他方式次數」，請簡述）",
            placeholder="例：視像通話 (Zoom)、寄送書信等",
        ).strip()
        mentor_remarks = st.text_area(
            "備註 ／ 簡述學童近況（選填）",
            placeholder="例：學童剛應付完考試，情緒良好...",
            height=80,
        ).strip()

        mentor_submitted = st.form_submit_button("確認提交友師記錄 🚀", use_container_width=True)

    if mentor_submitted:
        total_contacts = int(sms_count) + int(call_count) + int(f2f_count) + int(other_count)

        if not is_valid_3digit_id(mentor_stu_id):
            st.error("配對學生編號必須為 3 位數字（例如：105）！")
        elif not is_valid_phone4(mentor_phone4):
            st.error("友師登記電話最後 4 個字必須為 4 位數字！")
        elif no_contact and total_contacts > 0:
            st.error("你已勾選「本月份未有接觸學童」，請將各項接觸次數改回 0，或取消勾選該項目！")
        elif not no_contact and total_contacts == 0:
            st.error("若本月份有接觸學童，請至少填寫一項接觸次數；若本月完全未有接觸，請勾選「本月份未有接觸學童」！")
        elif not no_contact and int(other_count) > 0 and not other_details:
            st.error("你填寫了「其他方式次數」，請簡要填寫「其他接觸方式說明」！")
        else:
            row_data = {
                "Timestamp": now_hkt().strftime("%Y-%m-%d %H:%M:%S"),
                "所屬區域(District)": mentor_district,
                "學生編號(Student_ID)": mentor_stu_id,
                "友師電話最後4個字(Mentor_Phone_Last4)": mentor_phone4,
                "記錄月份(Year_Month)": mentor_month,
                "社交短訊次數(SMS_Count)": 0 if no_contact else int(sms_count),
                "電話通話次數(Call_Count)": 0 if no_contact else int(call_count),
                "面對面次數(F2F_Count)": 0 if no_contact else int(f2f_count),
                "其他次數(Other_Count)": 0 if no_contact else int(other_count),
                "未有接觸(No_Contact)": "是" if no_contact else "否",
                "其他方式說明(Other_Details)": "" if no_contact else other_details,
                "備註(Remarks)": mentor_remarks,
            }

            with st.spinner("正在提交友師接觸記錄..."):
                ok, result = call_gas_api(
                    {
                        "action": "append_row",
                        "sheet": "MentorLogs",
                        "row": row_data,
                    }
                )

            if ok:
                load_worksheet_for_lookup.clear()
                complete_form_submission(
                    "mentor",
                    f"🎉 友師接觸記錄已成功提交！已記錄學生編號：{mentor_stu_id}（{mentor_month}）",
                )
            else:
                st.error(f"提交失敗，請稍後重試（錯誤詳情：{result}）")


# ==================== 分頁五：紀錄查詢 ====================
with tab_lookup:
    st.write("#### 🔍 個人紀錄查詢")
    st.markdown(
        """
    <div class="privacy-notice">
        🔒 <b>私隱保障</b>：查詢時必須核對「所屬區域」、「編號最後 3 位數字」及「電話最後 4 個字」，系統不會公開任何姓名、完整電話或入數紙相片。
    </div>
    """,
        unsafe_allow_html=True,
    )

    lookup_role = st.radio(
        "請選擇查詢身分",
        ["👨👩👧 學員／家長查詢（已報名活動及存款紀錄）", "🤝 友師查詢（已上報每月接觸資料）"],
        horizontal=True,
    )

    with st.form("lookup_form"):
        l_col1, l_col2 = st.columns(2)
        with l_col1:
            q_district = st.selectbox("1. 所屬區域 *", DISTRICT_OPTIONS)
        with l_col2:
            q_id = st.text_input(
                "2. 計劃／學生編號（最後 3 位數字）*",
                placeholder="例：006、089、105",
                max_chars=3,
            ).strip()

        phone_label = (
            "3. 登記電話最後 4 個字（身分核對）*"
            if "學員" in lookup_role
            else "3. 友師登記電話最後 4 個字 *"
        )
        q_phone4 = st.text_input(phone_label, placeholder="例：1234", max_chars=4).strip()

        do_search = st.form_submit_button("查詢紀錄 🔍", use_container_width=True)

    if do_search:
        norm_dist = normalize_district(q_district)
        norm_id = normalize_id_last3(q_id)
        norm_phone = normalize_phone4(q_phone4)

        if not is_valid_3digit_id(q_id):
            st.error("請輸入 3 位數字計劃／學生編號（例如：006、089）！")
        elif not is_valid_phone4(q_phone4):
            st.error("電話最後 4 個字必須為 4 位數字！")
        else:
            # ---------- A. 學員／家長查詢 ----------
            if "學員" in lookup_role:
                st.write("---")
                st.write(f"##### 🎯 已報名活動紀錄（編號：{norm_id}）")
                try:
                    reg_df = load_worksheet_for_lookup("Registrations")
                    if not reg_df.empty:
                        dist_col = next((c for c in reg_df.columns if ("區" in c or "District" in c)), None)
                        id_col = next((c for c in reg_df.columns if ("編號" in c or "Participant_ID" in c)), None)
                        phone_col = next((c for c in reg_df.columns if ("電話" in c or "Phone" in c)), None)

                        mask = pd.Series(True, index=reg_df.index)
                        if dist_col:
                            mask &= reg_df[dist_col].apply(normalize_district) == norm_dist
                        if id_col:
                            mask &= reg_df[id_col].apply(normalize_id_last3) == norm_id
                        if phone_col:
                            mask &= reg_df[phone_col].apply(normalize_phone4) == norm_phone

                        matched_reg = reg_df[mask]
                        if matched_reg.empty:
                            st.info("暫未找到符合的活動報名紀錄（請確認分區、編號及電話尾 4 位是否與報名時一致）。")
                        else:
                            show_cols = [
                                c for c in ["Timestamp", "活動名稱(Activity_Title)", "活動類別(Activity_ID)", "參加人數(Headcount)"]
                                if c in matched_reg.columns
                            ]
                            disp_reg = matched_reg[show_cols].rename(
                                columns={
                                    "Timestamp": "提交時間",
                                    "活動名稱(Activity_Title)": "活動名稱",
                                    "活動類別(Activity_ID)": "活動類別",
                                    "參加人數(Headcount)": "參加人數",
                                }
                            )
                            st.dataframe(disp_reg, use_container_width=True, hide_index=True)
                    else:
                        st.info("目前未有任何活動報名紀錄。")
                except Exception as e:
                    st.warning(f"未能讀取活動報名紀錄（錯誤詳情：{e}）")

                st.write("---")
                st.write(f"##### 💰 已上載存款紀錄（編號：{norm_id}）")
                try:
                    sav_df = load_worksheet_for_lookup("Savings")
                    if not sav_df.empty:
                        dist_col = next((c for c in sav_df.columns if ("區" in c or "District" in c)), None)
                        id_col = next((c for c in sav_df.columns if ("編號" in c or "Student_ID" in c)), None)
                        phone_col = next((c for c in sav_df.columns if ("電話" in c or "Phone" in c)), None)

                        mask = pd.Series(True, index=sav_df.index)
                        if dist_col:
                            mask &= sav_df[dist_col].apply(normalize_district) == norm_dist
                        if id_col:
                            mask &= sav_df[id_col].apply(normalize_id_last3) == norm_id
                        if phone_col:
                            row_phones = sav_df[phone_col].apply(normalize_phone4)
                            if STRICT_SAVINGS_PHONE_CHECK:
                                mask &= row_phones == norm_phone
                            else:
                                # 新紀錄嚴格核對電話尾 4 位；舊紀錄未記錄電話時兼容顯示（不顯示姓名或相片）
                                mask &= (row_phones == norm_phone) | (row_phones == "")

                        matched_sav = sav_df[mask].copy()
                        if matched_sav.empty:
                            st.info("暫未找到符合的每月存款上載紀錄（請確認分區、編號及電話尾 4 位）。")
                        else:
                            if "上傳連結(Receipt_URL)" in matched_sav.columns:
                                matched_sav["入數紙憑證"] = matched_sav["上傳連結(Receipt_URL)"].apply(
                                    lambda u: "已上載 ✓" if str(u).startswith("http") else "待確認"
                                )
                            show_cols = [
                                c for c in ["存款月份(Year_Month)", "存款金額(Amount)", "存款日期(Deposit_Date)", "入數紙憑證", "審核狀態(Status)"]
                                if c in matched_sav.columns
                            ]
                            disp_sav = matched_sav[show_cols].rename(
                                columns={
                                    "存款月份(Year_Month)": "存款月份",
                                    "存款金額(Amount)": "金額 (HK$)",
                                    "存款日期(Deposit_Date)": "存款日期",
                                    "審核狀態(Status)": "核對狀態",
                                }
                            )
                            st.dataframe(disp_sav, use_container_width=True, hide_index=True)
                    else:
                        st.info("目前未有任何存款上載紀錄。")
                except Exception as e:
                    st.warning(f"未能讀取存款紀錄（錯誤詳情：{e}）")

            # ---------- B. 友師查詢 ----------
            else:
                st.write("---")
                st.write(f"##### 🤝 友師已上報接觸紀錄（學生編號：{norm_id}）")
                try:
                    mentor_df = load_worksheet_for_lookup("MentorLogs")
                    if not mentor_df.empty:
                        dist_col = next((c for c in mentor_df.columns if ("區" in c or "District" in c)), None)
                        id_col = next((c for c in mentor_df.columns if ("編號" in c or "Student_ID" in c)), None)
                        phone_col = next((c for c in mentor_df.columns if ("電話" in c or "Phone" in c)), None)

                        mask = pd.Series(True, index=mentor_df.index)
                        if dist_col:
                            mask &= mentor_df[dist_col].apply(normalize_district) == norm_dist
                        if id_col:
                            mask &= mentor_df[id_col].apply(normalize_id_last3) == norm_id
                        if phone_col:
                            mask &= mentor_df[phone_col].apply(normalize_phone4) == norm_phone

                        matched_m = mentor_df[mask]
                        if matched_m.empty:
                            st.info("暫未找到符合的友師接觸紀錄（請確認分區、配對學生編號及電話尾 4 位）。")
                        else:
                            show_cols = [
                                c
                                for c in [
                                    "記錄月份(Year_Month)",
                                    "社交短訊次數(SMS_Count)",
                                    "電話通話次數(Call_Count)",
                                    "面對面次數(F2F_Count)",
                                    "其他次數(Other_Count)",
                                    "未有接觸(No_Contact)",
                                    "Timestamp",
                                ]
                                if c in matched_m.columns
                            ]
                            disp_m = matched_m[show_cols].rename(
                                columns={
                                    "記錄月份(Year_Month)": "記錄月份",
                                    "社交短訊次數(SMS_Count)": "短訊",
                                    "電話通話次數(Call_Count)": "電話",
                                    "面對面次數(F2F_Count)": "面對面",
                                    "其他次數(Other_Count)": "其他",
                                    "未有接觸(No_Contact)": "未有接觸",
                                    "Timestamp": "提交時間",
                                }
                            )
                            st.dataframe(disp_m, use_container_width=True, hide_index=True)
                    else:
                        st.info("目前未有任何友師接觸紀錄。")
                except Exception as e:
                    st.warning(f"未能讀取友師紀錄（錯誤詳情：{e}）")

import sqlite3

import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(page_title="신규 권역 성장 기회 대시보드", layout="wide")
st.title("신규 권역 성장 기회 대시보드")                                  # ✏️ 6주차 메인 제목
st.caption("가입월 · 가입 채널 · 플랫폼별 | 2025.09 ~ 2026.07 가입자")     # ✏️ 6주차 부제

# ------------------------------------------------------------
# 0. 필요한 데이터만 추출 — 쓸 테이블과 컬럼만 메모리 DB에 올리고, 정제 테이블을 처음 한 번만 만듦
# ------------------------------------------------------------
DATA_DIR = "data"                          # 원본 CSV를 넣어 둔 폴더
NEEDED = {                                 # ✏️ 이번 대시보드에 필요한 테이블과 컬럼 (쿼리에 쓰는 것만)
    "app_events": ["event_datetime", "customer_id", "session_id", "event_name",
                   "platform", "traffic_source", "campaign_name"],
    "orders": ["customer_id", "order_datetime"],
}

@st.cache_resource
def get_db():                              # 필요한 컬럼만 메모리 DB에 올리고, 정제 테이블 events·odr을 생성(처음 한 번)
    con = sqlite3.connect(":memory:", check_same_thread=False)
    for name, cols in NEEDED.items():
        # dtype=str: 고객 ID의 앞자리 0(0000002)이 숫자로 바뀌지 않게 글자로 읽음
        pd.read_csv(f"{DATA_DIR}/{name}.csv", usecols=cols, dtype=str).to_sql(name, con, index=False)
    # ✏️ 5주차 제외 규칙: 테스트 계정(T로 시작) 제외 · 이벤트 100건 이상인 비정상 세션 제외
    con.execute("""
        CREATE TABLE events AS
        SELECT *
          FROM app_events
         WHERE customer_id NOT LIKE 'T%'
           AND session_id NOT IN (SELECT session_id FROM app_events
                                   GROUP BY session_id HAVING COUNT(*) >= 100)
    """)
    con.execute("CREATE TABLE odr AS SELECT * FROM orders WHERE customer_id NOT LIKE 'T%'")
    # 고객별로 묶는 집계가 빨라지도록 customer_id에 인덱스를 붙임
    con.execute("CREATE INDEX idx_events_customer ON events(customer_id)")
    con.execute("CREATE INDEX idx_odr_customer ON odr(customer_id)")
    return con

# ------------------------------------------------------------
# 1. 데이터 준비 — 정제된 테이블을 SQL로 집계 (비율이 아니라 분자 · 분모 건수만)
# ------------------------------------------------------------
@st.cache_data
def query(sql):                            # SQL을 실행해 표로 돌려줌
    return pd.read_sql(sql, get_db())

# 고객별 가입일 · 가입 채널 · 플랫폼 (가입 이벤트 기준) - 두 쿼리 모두 사용
SQL_SIGNUP = """
su AS (
    SELECT customer_id,
           DATE(MIN(event_datetime))                    AS 가입일,
           MIN(COALESCE(campaign_name, traffic_source)) AS 가입채널,
           MIN(platform)                                AS 플랫폼
      FROM events
     WHERE event_name = 'sign_up'
     GROUP BY customer_id
)
"""

# ✏️ 가입월 × 가입 채널 × 플랫폼별 가입 후 30일 퍼널 (예제 Q1 · Q3)
SQL_SIGNUP_FUNNEL = f"""
WITH {SQL_SIGNUP},
ev AS (                                     -- 가입 후 30일 안의 행동
    SELECT DISTINCT e.customer_id, e.event_name
      FROM events e
      JOIN su ON e.customer_id = su.customer_id
     WHERE julianday(DATE(e.event_datetime)) - julianday(su.가입일) BETWEEN 0 AND 30
),
fo AS (                                     -- 고객별 첫 주문일
    SELECT customer_id, DATE(MIN(order_datetime)) AS 첫주문일
      FROM odr
     GROUP BY customer_id
)
SELECT strftime('%Y-%m', su.가입일) AS 가입월,
       su.가입채널,
       su.플랫폼,
       COUNT(DISTINCT su.customer_id)                                                         AS 가입자,
       COUNT(DISTINCT CASE WHEN ev.event_name = 'view_restaurant' THEN su.customer_id END)    AS 식당조회,
       COUNT(DISTINCT CASE WHEN ev.event_name = 'add_to_cart'     THEN su.customer_id END)    AS 장바구니,
       COUNT(DISTINCT CASE WHEN ev.event_name = 'begin_checkout'  THEN su.customer_id END)    AS 결제시작,
       COUNT(DISTINCT CASE WHEN julianday(fo.첫주문일) - julianday(su.가입일) <= 30
                           THEN su.customer_id END)                                           AS 첫주문30일
  FROM su
  LEFT JOIN ev ON su.customer_id = ev.customer_id
  LEFT JOIN fo ON su.customer_id = fo.customer_id
 WHERE su.가입일 <= '2026-07-31'            -- 30일을 다 볼 수 있는 가입자만
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3
"""

# ✏️ 가입월 × 가입 채널 × 플랫폼별 30일 재주문 (예제 Q2, 분모가 첫 주문 고객이라 표를 따로 만듦)
SQL_REORDER = f"""
WITH {SQL_SIGNUP},
seq AS (
    SELECT customer_id, order_datetime,
           ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_datetime) AS 순번,
           LEAD(order_datetime) OVER (PARTITION BY customer_id ORDER BY order_datetime) AS 다음주문
      FROM odr
)
SELECT strftime('%Y-%m', su.가입일) AS 가입월,
       su.가입채널,
       su.플랫폼,
       COUNT(*)                                                                          AS 첫주문고객,
       SUM(CASE WHEN julianday(DATE(seq.다음주문)) - julianday(DATE(seq.order_datetime)) <= 30
                THEN 1 ELSE 0 END)                                                       AS 재주문30일
  FROM seq
  JOIN su ON seq.customer_id = su.customer_id
 WHERE seq.순번 = 1
   AND seq.order_datetime < '2026-08-01'    -- 30일을 다 볼 수 있는 첫 주문만
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3
"""

@st.cache_data
def load_data():
    return query(SQL_SIGNUP_FUNNEL), query(SQL_REORDER)

df, rd = load_data()                       # df: 가입 후 30일 퍼널 / rd: 30일 재주문

# 📌 AI 해설 사전 적용
AI_NOTES = {
    "전체": "전체 가입자 2774명의 30일 첫 주문 전환율은 69.9%이며 가장 낮은 전환율을 기록한 구간은 식당조회에서 장바구니로 이어지는 84.1% 구간이다. 2026-01 가입월 이후 전체 전환율이 하락세를 보이는 양상이 확인된다. 마케팅 캠페인이나 유입 경로의 변화가 전체 지표 하락에 영향을 미쳤는지 확인이 필요하다.",
    "Android": "안드로이드 플랫폼의 가입자는 1502명으로 30일 첫 주문 전환율은 69.2%이고 식당조회에서 장바구니 구간이 83.8%로 가장 낮다. 전체 평균과 유사한 흐름을 보이나 iOS 대비 소폭 낮은 수치를 기록했다. 플랫폼별 사용자 경험 차이가 전환율에 미친 영향에 대해 확인이 필요하다.",
    "iOS": "아이폰 플랫폼의 가입자는 1272명으로 30일 첫 주문 전환율은 70.7%이며 식당조회에서 장바구니 구간이 84.5%로 가장 낮다. 안드로이드 대비 전반적인 전환율이 소폭 높게 나타났다. 기기 환경에 따른 유저 행동 패턴의 차이에 대해 확인이 필요하다.",
    "organic": "오가닉 유입 가입자는 847명이며 30일 첫 주문 전환율은 75.2%로 전체 평균 대비 높고 식당조회에서 장바구니 구간이 88.6%로 가장 낮다. 광고를 통한 유입 집단에 비해 초기 이탈이 적은 편이다. 자연 유입 사용자의 서비스 충성도가 높은 원인에 대해 확인이 필요하다.",
    "INSTA_첫주문쿠폰": "인스타 첫주문쿠폰 채널의 가입자는 739명으로 30일 첫 주문 전환율은 55.6%에 그치고 식당조회에서 장바구니 구간이 72.4%로 가장 낮다. 2026-01 가입월부터 채널이 등장한 이후 전체 지표 하락을 견인한 주요 원인으로 보인다. 쿠폰 조건이나 유입된 고객의 성향이 구매 전환에 미친 영향에 대해 확인이 필요하다.",
    "NAVER_검색광고": "네이버 검색광고 채널의 가입자는 517명이며 30일 첫 주문 전환율은 76.4%로 높고 식당조회에서 장바구니 구간이 87.7%로 가장 낮다. 전체 평균 및 오가닉 유입과 비교해도 우수한 성과를 유지하고 있다. 검색 의도가 명확한 사용자의 유입 효율이 높게 작용한 것으로 보인다.",
    "GOOGLE_앱설치": "구글 앱설치 채널의 가입자는 398명이며 30일 첫 주문 전환율은 73.6%이며 다른 집단과 달리 장바구니에서 결제시작 구간이 88.0%로 가장 낮다. 타 채널과 비교해 장바구니 단계 이후의 결제 진입 장벽이 상대적으로 높다. 해당 구간에서의 사용자 이탈 원인에 대해 확인이 필요하다.",
    "친구초대": "친구초대 채널의 가입자는 273명으로 300명 미만이며 30일 첫 주문 전환율은 74.4%이고 식당조회에서 장바구니 구간이 87.1%로 가장 낮다. 소규모 집단임에도 30일 재주문율이 77.3%로 가장 높게 나타났다. 지인 추천을 통해 가입한 사용자의 서비스 잔존율이 높은 것으로 보인다."
}
AI_SOURCE = "🤖 Gemini가 미리 만든 해설 · 전체 기간 기준"

# ------------------------------------------------------------
# 2. 사이드바 필터 — 두 표에 같은 조건을 적용
# ------------------------------------------------------------
SMALL = 100                                # ✏️ 이보다 가입자가 적은 집단은 "작은 집단"

if "reset" not in st.session_state:
    st.session_state.reset = 0

def reset_filters():                       # 번호가 바뀌면 위젯이 처음 상태로 새로 만들어짐
    st.session_state.reset += 1

n = st.session_state.reset
months = sorted(df["가입월"].unique())
channels = sorted(df["가입채널"].unique())
platforms = sorted(df["플랫폼"].unique())

st.sidebar.header("필터")
start, end = st.sidebar.select_slider("가입월", options=months,
                                      value=(months[0], months[-1]), key=f"기간_{n}")
sel_channels = st.sidebar.multiselect("가입 채널", channels, default=channels, key=f"채널_{n}")
sel_platforms = st.sidebar.multiselect("플랫폼", platforms, default=platforms, key=f"플랫폼_{n}")
with st.sidebar.expander("고급 설정"):
    base_start, base_end = st.select_slider(
        "평소 범위 기준 기간", options=months, value=(months[0], months[3]), key=f"기준_{n}",   # ✏️ 문제가 없던 기간
        help="문제가 없던 기간을 고르세요. 이 기간의 월별 값 범위를 '평소 범위'로 씁니다.")
    hide_small = st.checkbox(f"가입자 {SMALL}명 미만 집단 제외 (집단 비교 · 드릴다운)", key=f"작은집단_{n}")
st.sidebar.button("필터 초기화", on_click=reset_filters)

# 집단 조건(가입 채널 · 플랫폼)만 적용한 데이터 g · rg, 기간까지 적용한 데이터 f · rf
g = df[df["가입채널"].isin(sel_channels) & df["플랫폼"].isin(sel_platforms)]
f = g[(g["가입월"] >= start) & (g["가입월"] <= end)]
rg = rd[rd["가입채널"].isin(sel_channels) & rd["플랫폼"].isin(sel_platforms)]
rf = rg[(rg["가입월"] >= start) & (rg["가입월"] <= end)]

def label(selected, all_values):           # 조건 줄에 쓸 말: 모두 고르면 "전체"
    if len(selected) == len(all_values):
        return "전체"
    return ", ".join(selected) if selected else "선택 없음"

period = "전체" if (start, end) == (months[0], months[-1]) else f"{start} ~ {end}"
st.caption(f"🔎 보고 있는 조건: 가입월 {period} · 가입 채널 {label(sel_channels, channels)} · "
           f"플랫폼 {label(sel_platforms, platforms)}")

if f.empty:
    st.warning("선택한 조건에 해당하는 데이터가 없습니다. 필터를 바꿔 주세요.")
    st.stop()

def rate(a, b):
    return a / b * 100 if b else 0

# ------------------------------------------------------------
# 3. 화면 — 탭 3개
# ------------------------------------------------------------
tab_overview, tab_explore, tab_action = st.tabs(["📊 개요", "🔍 탐색", "✅ 액션"])

# ===== [블록 A] 개요 · KPI 카드 =====
with tab_overview:
    st.caption("이 탭에서 볼 수 있는 것: 핵심 숫자와 그 숫자를 읽는 법, 6주차 리포트의 차트 3장 (필터 적용)")

    # 3-1. KPI 카드 — 5주차 지표 3개
    t = f[["가입자", "식당조회", "장바구니", "첫주문30일"]].sum()
    tr = rf[["첫주문고객", "재주문30일"]].sum()
    c1, c2, c3 = st.columns(3)
    c1.metric("30일 첫 주문 전환율", f"{rate(t['첫주문30일'], t['가입자']):.1f}%")
    c2.metric("식당 조회 → 장바구니 전환율", f"{rate(t['장바구니'], t['식당조회']):.1f}%")
    c3.metric("30일 재주문율", f"{rate(tr['재주문30일'], tr['첫주문고객']):.1f}%")

    with st.container(border=True):
        st.markdown("**📏 지금 숫자 읽기**")
        
        unique_months = sorted(f["가입월"].unique())
        if len(unique_months) < 3:
            msg = "비교하려면 선택 기간을 3개월 이상 골라 주세요"
            st.markdown(f"- 30일 첫 주문 전환율: {msg}")
            st.markdown(f"- 식당 조회 → 장바구니 전환율: {msg}")
        else:
            recent_3m = unique_months[-3:]
            target_f = f[f["가입월"].isin(recent_3m)]
            t_sum = target_f[["가입자", "식당조회", "장바구니", "첫주문30일"]].sum()
            
            cur_ord_rate = rate(t_sum["첫주문30일"], t_sum["가입자"])
            cur_cart_rate = rate(t_sum["장바구니"], t_sum["식당조회"])
            
            base_f = g[(g["가입월"] >= base_start) & (g["가입월"] <= base_end)]
            base_monthly = base_f.groupby("가입월")[["가입자", "식당조회", "장바구니", "첫주문30일"]].sum().reset_index()
            
            ord_rates = []
            cart_rates = []
            for _, r in base_monthly.iterrows():
                if r["가입자"] > 0:
                    ord_rates.append(r["첫주문30일"] / r["가입자"] * 100)
                if r["식당조회"] > 0:
                    cart_rates.append(r["장바구니"] / r["식당조회"] * 100)
            
            if len(base_monthly) < 3:
                base_msg = "기준 기간을 이 집단의 데이터가 있는 시기로 옮겨 주세요"
                st.markdown(f"- 30일 첫 주문 전환율: {base_msg}")
                st.markdown(f"- 식당 조회 → 장바구니 전환율: {base_msg}")
            else:
                ord_min, ord_max = min(ord_rates), max(ord_rates)
                cart_min, cart_max = min(cart_rates), max(cart_rates)
                
                start_label = recent_3m[0]
                
                def make_sentence(name, cur_val, min_v, max_v):
                    if cur_val < min_v:
                        diff_p = min_v - cur_val
                        return f"{start_label} 가입월부터 3개월 {name}은(는) {cur_val:.1f}%로, 평소 범위({min_v:.1f}–{max_v:.1f}%)보다 {diff_p:.1f}%p 낮습니다"
                    elif cur_val > max_v:
                        diff_p = cur_val - max_v
                        return f"{start_label} 가입월부터 3개월 {name}은(는) {cur_val:.1f}%로, 평소 범위({min_v:.1f}–{max_v:.1f}%)보다 {diff_p:.1f}%p 높습니다"
                    else:
                        return f"{start_label} 가입월부터 3개월 {name}은(는) {cur_val:.1f}%로, 평소 범위({min_v:.1f}–{max_v:.1f}%) 안에 있습니다"

                s1 = make_sentence("30일 첫 주문 전환율", cur_ord_rate, ord_min, ord_max)
                s2 = make_sentence("식당 조회 → 장바구니 전환율", cur_cart_rate, cart_min, cart_max)
                
                st.markdown(f"- {s1}")
                st.markdown(f"- {s2}")
                
        st.caption(f"📏 자동 계산 · 평소 범위 기준 기간 {base_start} ~ {base_end} (월별 값)")
        with st.expander("🤖 AI 해설 · 전체", expanded=False):
            st.write(AI_NOTES.get("전체", "아직 해설이 없습니다."))
        st.caption(AI_SOURCE)
# ===== [블록 A] 끝 =====

with tab_overview:
    st.divider()

    # ✏️ 3-2. 차트 - 6주차 차트 ① 문제 · ② 원인 · ③ 제안
    left, right = st.columns(2)

    # ① 문제: 가입월별 30일 첫 주문 전환율
    m = f.groupby("가입월")[["가입자", "첫주문30일"]].sum().reset_index()
    m["첫주문전환율"] = (m["첫주문30일"] / m["가입자"] * 100).round(1)
    fig1 = px.line(m, x="가입월", y="첫주문전환율", markers=True, hover_data=["가입자"],
                   labels={"가입월": "가입월", "첫주문전환율": "30일 첫 주문 전환율(%)", "가입자": "가입자(명)"},
                   title="① 가입월별 30일 첫 주문 전환율")
    fig1.update_layout(xaxis_title="")
    fig1.update_xaxes(type="category")       # 가입월(2025-09)을 날짜가 아니라 글자 그대로 표시
    left.plotly_chart(fig1, width="stretch")

    # ② 원인: 가입 채널별 식당 조회 → 장바구니 전환율
    ch = f.groupby("가입채널")[["식당조회", "장바구니"]].sum().reset_index()
    ch["장바구니전환율"] = (ch["장바구니"] / ch["식당조회"] * 100).round(1)
    ch = ch.sort_values("장바구니전환율")
    fig2 = px.bar(ch, x="장바구니전환율", y="가입채널", orientation="h", text_auto=".1f",
                  hover_data=["식당조회"],
                  labels={"장바구니전환율": "식당 조회 → 장바구니 전환율(%)", "가입채널": "가입 채널",
                          "식당조회": "식당 조회 가입자(명)"},
                  title="② 가입 채널별 식당 조회 → 장바구니 전환율")
    fig2.update_xaxes(range=[0, ch["장바구니전환율"].max() * 1.2])
    fig2.update_layout(yaxis_title="")
    right.plotly_chart(fig2, width="stretch")

    # ③ 제안: 가입 채널별 30일 재주문율
    rc = rf.groupby("가입채널")[["첫주문고객", "재주문30일"]].sum().reset_index()
    rc["재주문율"] = (rc["재주문30일"] / rc["첫주문고객"] * 100).round(1)
    rc = rc.sort_values("재주문율")
    fig3 = px.bar(rc, x="재주문율", y="가입채널", orientation="h", text_auto=".1f",
                  hover_data=["첫주문고객"],
                  labels={"재주문율": "30일 재주문율(%)", "가입채널": "가입 채널", "첫주문고객": "첫 주문 고객(명)"},
                  title="③ 가입 채널별 30일 재주문율")
    fig3.update_xaxes(range=[0, rc["재주문율"].max() * 1.2])
    fig3.update_layout(yaxis_title="")
    st.plotly_chart(fig3, width="stretch")

    # AI 해설용 데이터 내려받기
    with st.expander("📥 AI 해설용 데이터 내려받기"):
        st.download_button("가입 후 30일 퍼널 (가입월 × 가입 채널 × 플랫폼)",
                           df.to_csv(index=False).encode("utf-8-sig"), "가입월_채널_플랫폼_퍼널.csv")
        st.download_button("30일 재주문 (가입월 × 가입 채널 × 플랫폼)",
                           rd.to_csv(index=False).encode("utf-8-sig"), "가입월_채널_플랫폼_재주문.csv")

# ===== [블록 B] 탐색 — 집단 비교 · 드릴다운 =====
with tab_explore:
    st.caption("이 탭에서 볼 수 있는 것: 집단 비교와 드릴다운 — 어디서, 언제부터인가")

    compare_target = st.radio("비교 기준", ["가입채널", "플랫폼"], horizontal=True)

    steps = ["가입자", "식당조회", "장바구니", "결제시작", "첫주문30일"]
    df_compare = f.groupby(compare_target)[steps].sum().reset_index()

    if hide_small:
        small_groups = df_compare[df_compare["가입자"] < SMALL][compare_target].tolist()
        if small_groups:
            st.caption(f"제외한 집단: {', '.join(small_groups)} (가입자 {SMALL}명 미만)")
            df_compare = df_compare[df_compare["가입자"] >= SMALL]

    if df_compare.empty:
        st.warning("조건에 해당하는 집단이 없습니다.")
    else:
        warning_groups = df_compare[df_compare["가입자"] < SMALL][compare_target].tolist()
        if warning_groups:
            st.caption(f"안내: {', '.join(warning_groups)} 집단은 가입자가 {SMALL}명 미만으로 값이 크게 흔들릴 수 있습니다.")

        left, right = st.columns(2)

        df_funnel_melt = pd.DataFrame()
        for _, row in df_compare.iterrows():
            group_name = row[compare_target]
            base_count = row["가입자"]
            for step in steps:
                val = row[step]
                pct = (val / base_count * 100) if base_count else 0
                df_funnel_melt = pd.concat([df_funnel_melt, pd.DataFrame([{
                    compare_target: group_name,
                    "퍼널단계": step,
                    "누적전환율": round(pct, 1),
                    "사람수": val
                }])])

        fig_funnel = px.funnel(
            df_funnel_melt,
            x="누적전환율",
            y="퍼널단계",
            color=compare_target,
            hover_data=["사람수"],
            labels={
                "누적전환율": "누적 전환율(%)",
                "퍼널단계": "퍼널 단계",
                compare_target: "집단",
                "사람수": "사람 수(명)"
            },
            title="집단별 누적 전환율 퍼널"
        )
        left.plotly_chart(fig_funnel, width="stretch")

        df_rate_melt = pd.DataFrame()
        pairs = [
            ("가입자", "식당조회"),
            ("식당조회", "장바구니"),
            ("장바구니", "결제시작"),
            ("결제시작", "첫주문30일")
        ]
        for _, row in df_compare.iterrows():
            group_name = row[compare_target]
            for prev_step, curr_step in pairs:
                p_val = row[prev_step]
                c_val = row[curr_step]
                rate_val = (c_val / p_val * 100) if p_val else 0
                df_rate_melt = pd.concat([df_rate_melt, pd.DataFrame([{
                    compare_target: group_name,
                    "구간": f"{prev_step} → {curr_step}",
                    "단계전환율": round(rate_val, 1),
                    "직전단계사람수": p_val,
                    "현재단계사람수": c_val
                }])])

        fig_bar = px.bar(
            df_rate_melt,
            x="구간",
            y="단계전환율",
            color=compare_target,
            barmode="group",
            text="단계전환율",
            hover_data=["직전단계사람수", "현재단계사람수"],
            labels={
                "구간": "단계 구간",
                "단계전환율": "단계 전환율(%)",
                compare_target: "집단",
                "직전단계사람수": "직전 단계 사람 수(명)",
                "현재단계사람수": "현재 단계 사람 수(명)"
            },
            title="집단별 단계 전환율 비교"
        )
        fig_bar.update_traces(texttemplate='%{text:.1f}%', textposition='outside')
        right.plotly_chart(fig_bar, width="stretch")

    st.divider()
    st.markdown("### 드릴다운: 언제부터, 무엇 때문일까")

    available_groups = df_compare[compare_target].tolist() if not df_compare.empty else []
    if available_groups:
        d_col1, d_col2 = st.columns(2)
        with d_col1:
            selected_group = st.selectbox("자세히 볼 집단", available_groups)
        with d_col2:
            step_options = [
                "가입 → 30일 첫 주문",
                "가입 → 식당조회",
                "식당조회 → 장바구니",
                "장바구니 → 결제시작",
                "결제시작 → 30일 첫 주문"
            ]
            selected_step = st.selectbox("단계", step_options)

        other_axis = "플랫폼" if compare_target == "가입채널" else "가입채널"

        sub_f = f[f[compare_target] == selected_group]

        grouped_sub = sub_f.groupby(["가입월", other_axis])[["가입자", "식당조회", "장바구니", "결제시작", "첫주문30일"]].sum().reset_index()

        if selected_step == "가입 → 30일 첫 주문":
            num_col, den_col = "첫주문30일", "가입자"
        elif selected_step == "가입 → 식당조회":
            num_col, den_col = "식당조회", "가입자"
        elif selected_step == "식당조회 → 장바구니":
            num_col, den_col = "장바구니", "식당조회"
        elif selected_step == "장바구니 → 결제시작":
            num_col, den_col = "결제시작", "장바구니"
        else:
            num_col, den_col = "첫주문30일", "결제시작"

        grouped_sub["전환율"] = grouped_sub.apply(
            lambda r: round(r[num_col] / r[den_col] * 100, 1) if r[den_col] > 0 else None, axis=1
        )

        fig_drill1 = px.line(
            grouped_sub,
            x="가입월",
            y="전환율",
            color=other_axis,
            markers=True,
            hover_data=[num_col, den_col],
            labels={
                "가입월": "가입월",
                "전환율": "전환율(%)",
                other_axis: other_axis,
                num_col: "분자 사람 수(명)",
                den_col: "직전 단계 사람 수(명)"
            },
            title=f"① {selected_group}의 가입월별 {selected_step} 전환율 — {other_axis}별로 쪼개 보기"
        )
        fig_drill1.update_xaxes(type="category")
        st.plotly_chart(fig_drill1, width="stretch")
        st.caption("모든 갈래가 함께 움직였다면, 그 축은 원인이 아닙니다.")

        f_copy = f.copy()
        f_copy["그룹구분"] = f_copy[compare_target].apply(lambda x: selected_group if x == selected_group else "그 외")

        bar_grouped = f_copy.groupby(["가입월", "그룹구분"])["가입자"].sum().reset_index()

        fig_drill2 = px.bar(
            bar_grouped,
            x="가입월",
            y="가입자",
            color="그룹구분",
            barmode="stack",
            color_discrete_map={selected_group: "#1f77b4", "그 외": "#d3d3d3"},
            labels={
                "가입월": "가입월",
                "가입자": "가입자 수(명)",
                "그룹구분": "집단 구분"
            },
            title=f"② 가입월별 가입자 수 구성 ({selected_group} vs 그 외)"
        )
        fig_drill2.update_xaxes(type="category")
        st.plotly_chart(fig_drill2, width="stretch")
        st.caption("전체 전환율이 움직인 시점에 이 집단의 비중이 함께 바뀌었다면 구성 변화가 원인 후보입니다. 함께 움직였다고 원인이 확정되지는 않습니다.")

        with st.expander(f"🤖 AI 해설 · {selected_group}", expanded=False):
            st.write(AI_NOTES.get(selected_group, "이 집단의 해설은 아직 없습니다."))
        st.caption(AI_SOURCE)
# ===== [블록 B] 끝 =====

# ===== [블록 C] 액션 =====
with tab_action:
    st.caption("측정 KPI의 현재 값은 선택한 기간의 마지막 3개월로 계산합니다")
    
    f_action = df[(df["가입월"] >= start) & (df["가입월"] <= end)]
    rd_action = rd[(rd["가입월"] >= start) & (rd["가입월"] <= end)]
    
    unique_months_act = sorted(f_action["가입월"].unique()) if not f_action.empty else []
    
    if len(unique_months_act) >= 3:
        last_3m = unique_months_act[-3:]
        f_3m = f_action[f_action["가입월"].isin(last_3m)]
        rd_3m = rd_action[rd_action["가입월"].isin(last_3m)]
        
        t_act = f_3m[["가입자", "식당조회", "장바구니", "첫주문30일"]].sum()
        tr_act = rd_3m[["첫주문고객", "재주문30일"]].sum()
        
        val1 = rate(t_act['첫주문30일'], t_act['가입자']) if t_act['가입자'] > 0 else None
        val2 = rate(tr_act['재주문30일'], tr_act['첫주문고객']) if tr_act['첫주문고객'] > 0 else None
    else:
        val1 = None
        val2 = None

    col_a, col_b = st.columns(2)
    
    with col_a:
        with st.container(border=True):
            st.markdown("카드 1 · 인스타 채널 첫주문쿠폰 효율 개선")
            st.markdown("- 문제: INSTA_첫주문쿠폰 채널의 30일 첫 주문 전환율이 55.6%로 전체 평균(69.9%) 대비 크게 낮다.")
            st.markdown("- 근거: 2026-01월 도입 이후 전체 전환율 하락을 견인했으며 식당조회→장바구니 전환율도 72.4%로 저조하다.")
            st.markdown("- 성격: 구조적 마케팅 채널 효율 저하")
            st.markdown("- 액션: 인스타 유입 고객의 온보딩 화면 및 첫주문 쿠폰 적용 조건을 점검하고 타 채널 유입 흐름과 비교 분석한다.")
            st.markdown("- 우선순위: 높음")
            
            if val1 is not None:
                st.metric("30일 첫 주문 전환율 (최근 3개월)", f"{val1:.1f}%")
            else:
                st.metric("30일 첫 주문 전환율 (최근 3개월)", "데이터 없음")
            st.caption("대시보드에서: 📊 개요 탭 및 🔍 탐색 탭의 채널별 퍼널 비교")
            
    with col_b:
        with st.container(border=True):
            st.markdown("카드 2 · 결제 단계 이탈 구간 최적화")
            st.markdown("- 문제: 구글 앱설치 채널 등에서 장바구니→결제시작 단계의 전환율이 다소 낮게 나타나는 경향이 있다.")
            st.markdown("- 근거: 세부 퍼널 분석 시 특정 구간에서의 결제 진입 장벽이 확인된다.")
            st.markdown("- 성격: 결제 UX 및 프로세스 개선 필요")
            st.markdown("- 액션: 결제 시작 직전 단계의 UI 오류 여부를 확인하고 간편결제 연동 상태를 점검한다.")
            st.markdown("- 우선순위: 보통")
            
            if val2 is not None:
                st.metric("30일 재주문율 (최근 3개월)", f"{val2:.1f}%")
            else:
                st.metric("30일 재주문율 (최근 3개월)", "데이터 없음")
            st.caption("대시보드에서: 📊 개요 탭의 재주문율 지표 및 🔍 탐색 탭")
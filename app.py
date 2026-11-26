import sqlite3
import calendar
from datetime import date, datetime, timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd
import requests
import streamlit as st

# ============================================================
# MUTUAL FUND NAV TRACKER + SIP XIRR CALCULATOR
# ============================================================

DB_NAME = "mutual_fund.db"
AMFI_URL = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"

PALETTE = ["#17365d", "#2e7d9a", "#e07a1f", "#4c9f70", "#a23b72",
           "#c9a227", "#6c757d", "#8e5ea2", "#d1495b", "#3d5a80"]

st.set_page_config(page_title="Mutual Fund NAV Tracker", page_icon="📈", layout="wide")

# ============================================================
# STYLE
# ============================================================

st.markdown("""
<style>
.stApp { background-color: #f5f7fb; }
.block-container { padding-top: 2rem; padding-bottom: 3rem; max-width: 1400px; }
h1 { color: #17365d !important; font-weight: 700 !important; }
h2 { color: #17365d !important; font-weight: 650 !important; }
h3 { color: #244a73 !important; font-weight: 600 !important; }
section[data-testid="stSidebar"] { background-color: #17365d; }
section[data-testid="stSidebar"] * { color: white !important; }
div[data-testid="stMetric"] {
    background-color: white; padding: 20px; border-radius: 12px;
    border: 1px solid #dce3ec; box-shadow: 0 3px 10px rgba(0,0,0,0.06);
}
div[data-testid="stMetricLabel"] { color: #607080 !important; font-weight: 600; }
div[data-testid="stMetricValue"] { color: #17365d !important; font-weight: 700; }
.stButton > button {
    background-color: #17365d !important; color: #ffffff !important;
    border-radius: 8px !important; border: none !important;
    padding: 0.65rem 1.3rem !important; font-weight: 700 !important; font-size: 16px !important;
}
.stButton > button p { color: #ffffff !important; }
.stButton > button:hover { background-color: #244a73 !important; }
input, textarea { border-radius: 7px !important; }
div[data-testid="stAlert"] { border-radius: 9px; }
hr { border-color: #dce3ec; }
</style>
""", unsafe_allow_html=True)

# ============================================================
# DATE HELPERS
# Dates are stored in the DB as ISO text (YYYY-MM-DD) so that
# string comparison / ORDER BY matches chronological order.
# (The old dd-Mon-YYYY format sorted alphabetically = wrong NAVs.)
# ============================================================

def to_iso(d):
    return d.strftime("%Y-%m-%d")


def from_iso(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def pretty(s):
    """ISO string -> dd-Mon-YYYY for display."""
    try:
        return from_iso(s).strftime("%d-%b-%Y")
    except (ValueError, TypeError):
        return str(s)


def add_months(d, months, day=None):
    """Add months to a date, clamping the day to the month length."""
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    wanted = day if day else d.day
    return date(year, month, min(wanted, calendar.monthrange(year, month)[1]))


# ============================================================
# DATABASE
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_NAME, timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


_MONTH_CASE = """CASE substr({c},4,3)
    WHEN 'Jan' THEN '01' WHEN 'Feb' THEN '02' WHEN 'Mar' THEN '03'
    WHEN 'Apr' THEN '04' WHEN 'May' THEN '05' WHEN 'Jun' THEN '06'
    WHEN 'Jul' THEN '07' WHEN 'Aug' THEN '08' WHEN 'Sep' THEN '09'
    WHEN 'Oct' THEN '10' WHEN 'Nov' THEN '11' WHEN 'Dec' THEN '12' END"""


def migrate_old_dates(conn):
    """Convert legacy dd-Mon-YYYY rows to YYYY-MM-DD (safe to run repeatedly)."""
    cur = conn.cursor()
    for table, col in (("nav_history", "nav_date"), ("sip_transactions", "transaction_date")):
        month_sql = _MONTH_CASE.format(c=col)
        cur.execute(f"""
            UPDATE OR IGNORE {table}
            SET {col} = substr({col},8,4) || '-' || {month_sql} || '-' || substr({col},1,2)
            WHERE {col} LIKE '__-___-____'
        """)
        # Rows that collided with an existing ISO row are duplicates -> drop
        cur.execute(f"DELETE FROM {table} WHERE {col} LIKE '__-___-____'")
    conn.commit()


def setup_database():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS schemes (
            scheme_id INTEGER PRIMARY KEY AUTOINCREMENT,
            amfi_code TEXT UNIQUE,
            scheme_name TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS nav_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scheme_id INTEGER NOT NULL,
            nav_date TEXT NOT NULL,
            nav REAL NOT NULL,
            UNIQUE(scheme_id, nav_date),
            FOREIGN KEY(scheme_id) REFERENCES schemes(scheme_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sip_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scheme_id INTEGER NOT NULL,
            transaction_date TEXT NOT NULL,
            amount REAL NOT NULL,
            units REAL NOT NULL,
            FOREIGN KEY(scheme_id) REFERENCES schemes(scheme_id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_nav_scheme_date ON nav_history(scheme_id, nav_date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_scheme_code ON schemes(amfi_code)")
    conn.commit()

    migrate_old_dates(conn)
    conn.close()


setup_database()

# ============================================================
# AMFI DOWNLOAD (monthly chunks, 3 retries each)
# ============================================================

def download_amfi(start_date, end_date):
    all_data = []
    current_start = start_date

    while current_start <= end_date:
        next_month = add_months(date(current_start.year, current_start.month, 1), 1)
        current_end = min(next_month - timedelta(days=1), end_date)

        params = {
            "frmdt": current_start.strftime("%d-%b-%Y"),
            "todt": current_end.strftime("%d-%b-%Y"),
            "tp": "1",
        }

        success = False
        for _ in range(3):
            try:
                response = requests.get(
                    AMFI_URL, params=params, timeout=180,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                response.raise_for_status()
                if response.text.strip():
                    all_data.append(response.text)
                    success = True
                    break
            except requests.exceptions.RequestException:
                continue

        if not success:
            raise RuntimeError(
                f"AMFI download failed for {current_start.strftime('%d-%b-%Y')} "
                f"to {current_end.strftime('%d-%b-%Y')}"
            )

        current_start = current_end + timedelta(days=1)

    return "\n".join(all_data)


# ============================================================
# IMPORT NAV DATA
# ============================================================

DATE_FORMATS = ("%d-%b-%Y", "%d-%B-%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d")


def parse_date(value):
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def parse_nav_line(parts):
    """Return (nav, iso_date) from a split AMFI line, or (None, None)."""
    nav, nav_date = None, None
    for value in parts[2:]:
        value = value.strip()
        if nav_date is None:
            d = parse_date(value)
            if d:
                nav_date = to_iso(d)
                continue
        if nav is None:
            try:
                v = float(value)
                if v > 0:
                    nav = v
            except ValueError:
                pass
    return nav, nav_date


def import_nav_data(text):
    conn = get_db()
    cur = conn.cursor()

    parsed = matched = skipped = 0
    rows_to_insert = []

    # Cache scheme ids once instead of one SELECT per line
    scheme_ids = {code: sid for sid, code in cur.execute("SELECT scheme_id, amfi_code FROM schemes")}

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        parts = [p.strip() for p in line.split(";")]

        if len(parts) < 6:
            skipped += 1
            continue

        scheme_code, scheme_name = parts[0], parts[1]

        if not scheme_code.isdigit():
            # headers and fund-house/category lines
            if scheme_code.lower().replace(" ", "").replace("_", "") != "schemecode":
                skipped += 1
            continue

        nav, nav_date = parse_nav_line(parts)
        if nav is None or nav_date is None:
            skipped += 1
            continue

        scheme_id = scheme_ids.get(scheme_code)
        if scheme_id is None:
            cur.execute(
                "INSERT OR IGNORE INTO schemes (amfi_code, scheme_name) VALUES (?, ?)",
                (scheme_code, scheme_name),
            )
            cur.execute("SELECT scheme_id FROM schemes WHERE amfi_code = ?", (scheme_code,))
            scheme_id = cur.fetchone()[0]
            scheme_ids[scheme_code] = scheme_id
        else:
            matched += 1

        parsed += 1
        rows_to_insert.append((scheme_id, nav_date, nav))

    before = conn.total_changes
    cur.executemany(
        "INSERT OR IGNORE INTO nav_history (scheme_id, nav_date, nav) VALUES (?, ?, ?)",
        rows_to_insert,
    )
    inserted = conn.total_changes - before

    conn.commit()
    conn.close()

    return {"parsed": parsed, "matched": matched, "inserted": inserted, "skipped": skipped}


# ============================================================
# QUERIES
# ============================================================

def search_schemes(search_text):
    conn = get_db()
    cur = conn.cursor()
    like = "%" + search_text.strip() + "%"
    cur.execute("""
        SELECT scheme_id, amfi_code, scheme_name
        FROM schemes
        WHERE scheme_name LIKE ? OR amfi_code LIKE ?
        ORDER BY scheme_name
        LIMIT 100
    """, (like, like))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_nav(scheme_id, iso_date):
    """NAV on or before the given ISO date."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT nav FROM nav_history
        WHERE scheme_id = ? AND nav_date <= ?
        ORDER BY nav_date DESC LIMIT 1
    """, (scheme_id, iso_date))
    result = cur.fetchone()
    conn.close()
    return float(result[0]) if result else None


def get_latest_nav(scheme_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT nav_date, nav FROM nav_history
        WHERE scheme_id = ? ORDER BY nav_date DESC LIMIT 1
    """, (scheme_id,))
    result = cur.fetchone()
    conn.close()
    return result


def get_nav_series(scheme_id, start_iso=None, end_iso=None):
    conn = get_db()
    query = "SELECT nav_date, nav FROM nav_history WHERE scheme_id = ?"
    params = [scheme_id]
    if start_iso:
        query += " AND nav_date >= ?"
        params.append(start_iso)
    if end_iso:
        query += " AND nav_date <= ?"
        params.append(end_iso)
    query += " ORDER BY nav_date"
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    if not df.empty:
        df["nav_date"] = pd.to_datetime(df["nav_date"])
    return df


def get_transactions():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT t.id, t.scheme_id, s.amfi_code, s.scheme_name,
               t.transaction_date, t.amount, t.units
        FROM sip_transactions t
        JOIN schemes s ON t.scheme_id = s.scheme_id
        ORDER BY t.transaction_date, t.id
    """)
    rows = cur.fetchall()
    conn.close()
    return rows


def delete_transaction(transaction_id):
    conn = get_db()
    conn.execute("DELETE FROM sip_transactions WHERE id = ?", (transaction_id,))
    conn.commit()
    conn.close()


# ============================================================
# XIRR
# ============================================================

def xnpv(rate, cashflows):
    first_date = cashflows[0][0]
    return sum(
        amount / ((1 + rate) ** ((d - first_date).days / 365.0))
        for d, amount in cashflows
    )


def calculate_xirr(cashflows):
    cashflows = sorted(cashflows, key=lambda x: x[0])

    if len(cashflows) < 2:
        return None
    if not any(x[1] > 0 for x in cashflows) or not any(x[1] < 0 for x in cashflows):
        return None

    first_date = cashflows[0][0]

    # Newton's method
    rate = 0.10
    for _ in range(100):
        try:
            npv = 0.0
            derivative = 0.0
            for d, amount in cashflows:
                years = (d - first_date).days / 365.0
                npv += amount / ((1 + rate) ** years)
                derivative -= years * amount / ((1 + rate) ** (years + 1))

            if abs(npv) < 1e-7:
                return rate
            if derivative == 0:
                break

            new_rate = rate - npv / derivative
            if new_rate <= -0.999999 or abs(new_rate) > 1e6:
                break
            rate = new_rate
        except (OverflowError, ZeroDivisionError):
            break

    # Fallback: bisection
    low, high = -0.9999, 10.0
    try:
        low_value = xnpv(low, cashflows)
        high_value = xnpv(high, cashflows)
        for _ in range(30):
            if low_value * high_value <= 0:
                break
            high *= 2
            high_value = xnpv(high, cashflows)
    except (OverflowError, ZeroDivisionError):
        return None

    if low_value * high_value > 0:
        return None

    for _ in range(200):
        mid = (low + high) / 2
        try:
            mid_value = xnpv(mid, cashflows)
        except (OverflowError, ZeroDivisionError):
            return None
        if abs(mid_value) < 1e-7:
            return mid
        if low_value * mid_value <= 0:
            high = mid
        else:
            low = mid
            low_value = mid_value

    return (low + high) / 2


# ============================================================
# SAVE TRANSACTION
# ============================================================

def save_transaction(scheme_id, tx_date, amount):
    """Returns (ok, units, message)."""
    iso = to_iso(tx_date)
    nav = get_nav(scheme_id, iso)

    if nav is None:
        return False, 0.0, f"NAV not available on or before {pretty(iso)}."

    units = amount / nav

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT id FROM sip_transactions
        WHERE scheme_id = ? AND transaction_date = ? AND ABS(amount - ?) < 0.005
    """, (scheme_id, iso, amount))

    if cur.fetchone():
        conn.close()
        return False, units, f"Duplicate: an investment of ₹{amount:,.2f} on {pretty(iso)} already exists."

    cur.execute("""
        INSERT INTO sip_transactions (scheme_id, transaction_date, amount, units)
        VALUES (?, ?, ?, ?)
    """, (scheme_id, iso, amount, units))
    conn.commit()
    conn.close()

    return True, units, "Saved."


# ============================================================
# CHART HELPERS (matplotlib)
# ============================================================

def style_axes(ax, title, xlabel="", ylabel=""):
    ax.set_title(title, fontsize=13, fontweight="bold", color="#17365d", loc="left", pad=12)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.25, linestyle="--")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


def rupee_formatter():
    def fmt(x, _pos):
        ax_ = abs(x)
        if ax_ >= 1e7:
            return f"₹{x / 1e7:.1f}Cr"
        if ax_ >= 1e5:
            return f"₹{x / 1e5:.1f}L"
        if ax_ >= 1e3:
            return f"₹{x / 1e3:.0f}K"
        return f"₹{x:.0f}"
    return mticker.FuncFormatter(fmt)


def _stretch(func, *args, **kwargs):
    """Full-width rendering that works on both old and new Streamlit versions."""
    try:
        return func(*args, width="stretch", **kwargs)
    except TypeError:
        return func(*args, use_container_width=True, **kwargs)


def show_fig(fig):
    fig.tight_layout()
    _stretch(st.pyplot, fig)
    plt.close(fig)


def plot_nav_history(df, name, show_ma=True):
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.plot(df["nav_date"], df["nav"], color=PALETTE[0], linewidth=1.8, label="NAV")

    if show_ma and len(df) >= 30:
        ma = df["nav"].rolling(30).mean()
        ax.plot(df["nav_date"], ma, color=PALETTE[2], linewidth=1.4, linestyle="--", label="30-day average")

    hi, lo = df["nav"].idxmax(), df["nav"].idxmin()
    ax.scatter(df.loc[hi, "nav_date"], df.loc[hi, "nav"], color="#4c9f70", zorder=5, label=f"High ₹{df.loc[hi, 'nav']:.2f}")
    ax.scatter(df.loc[lo, "nav_date"], df.loc[lo, "nav"], color="#d1495b", zorder=5, label=f"Low ₹{df.loc[lo, 'nav']:.2f}")

    ax.fill_between(df["nav_date"], df["nav"], df["nav"].min(), color=PALETTE[0], alpha=0.07)
    style_axes(ax, name[:90], "Date", "NAV (₹)")
    ax.legend(frameon=False, loc="best")
    fig.autofmt_xdate()
    return fig


def plot_invested_vs_value(summary):
    fig, ax = plt.subplots(figsize=(11, 4.5))
    n = len(summary)
    x = range(n)
    w = 0.38
    ax.bar([i - w / 2 for i in x], summary["Invested"], w, label="Invested", color="#9db4cf")
    ax.bar([i + w / 2 for i in x], summary["Current Value"], w, label="Current value", color=PALETTE[0])

    for i, (inv, val) in enumerate(zip(summary["Invested"], summary["Current Value"])):
        pct = (val - inv) / inv * 100 if inv else 0
        ax.text(i + w / 2, val, f"{pct:+.1f}%", ha="center", va="bottom", fontsize=9,
                color="#4c9f70" if pct >= 0 else "#d1495b", fontweight="bold")

    labels = [s if len(s) <= 28 else s[:26] + "…" for s in summary["Scheme"]]
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=20, ha="right")
    ax.yaxis.set_major_formatter(rupee_formatter())
    style_axes(ax, "Invested vs Current Value (by scheme)")
    ax.legend(frameon=False)
    return fig


def plot_allocation(summary):
    fig, ax = plt.subplots(figsize=(6.5, 5))
    values = summary["Current Value"]
    if values.sum() <= 0:
        values = summary["Invested"]
    labels = [s if len(s) <= 24 else s[:22] + "…" for s in summary["Scheme"]]
    ax.pie(
        values, labels=labels, autopct="%1.1f%%", startangle=90,
        colors=PALETTE[: len(values)], wedgeprops={"width": 0.45, "edgecolor": "white"},
        textprops={"fontsize": 9},
    )
    ax.set_title("Portfolio Allocation", fontsize=13, fontweight="bold", color="#17365d", loc="left")
    return fig


def plot_growth(timeline):
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.plot(timeline.index, timeline["Value"], color=PALETTE[0], linewidth=2, label="Portfolio value")
    ax.step(timeline.index, timeline["Invested"], where="post", color=PALETTE[2], linewidth=1.6, label="Amount invested")
    ax.fill_between(
        timeline.index, timeline["Invested"], timeline["Value"],
        where=timeline["Value"] >= timeline["Invested"], color="#4c9f70", alpha=0.18, interpolate=True,
    )
    ax.fill_between(
        timeline.index, timeline["Invested"], timeline["Value"],
        where=timeline["Value"] < timeline["Invested"], color="#d1495b", alpha=0.18, interpolate=True,
    )
    ax.yaxis.set_major_formatter(rupee_formatter())
    style_axes(ax, "Portfolio Growth Over Time")
    ax.legend(frameon=False, loc="upper left")
    fig.autofmt_xdate()
    return fig


def plot_xirr_bars(summary):
    data = summary.dropna(subset=["XIRR %"])
    if data.empty:
        return None
    fig, ax = plt.subplots(figsize=(11, 3.8))
    colors = ["#4c9f70" if v >= 0 else "#d1495b" for v in data["XIRR %"]]
    labels = [s if len(s) <= 30 else s[:28] + "…" for s in data["Scheme"]]
    bars = ax.barh(labels, data["XIRR %"], color=colors)
    ax.bar_label(bars, labels=[f"{v:.2f}%" for v in data["XIRR %"]], padding=3, fontsize=9)
    ax.axvline(0, color="#444", linewidth=0.8)
    style_axes(ax, "XIRR by Scheme (annualised)", "XIRR %")
    ax.invert_yaxis()
    return fig


# ============================================================
# PORTFOLIO CALCULATIONS
# ============================================================

def build_portfolio(transactions):
    """Returns (summary_df, portfolio_cashflows, valuation_date)."""
    per_scheme = {}
    latest_dates = []

    for _id, scheme_id, _code, name, tx_iso, amount, units in transactions:
        try:
            d = from_iso(tx_iso)
        except ValueError:
            continue
        s = per_scheme.setdefault(scheme_id, {"name": name, "flows": [], "invested": 0.0, "units": 0.0})
        s["flows"].append((d, -float(amount)))
        s["invested"] += float(amount)
        s["units"] += float(units)

    rows = []
    all_flows = []
    last_tx = max((f[0] for s in per_scheme.values() for f in s["flows"]), default=date.today())

    for scheme_id, s in per_scheme.items():
        latest = get_latest_nav(scheme_id)
        if latest:
            nav_date, nav = from_iso(latest[0]), float(latest[1])
            latest_dates.append(nav_date)
        else:
            nav_date, nav = None, 0.0

        value = s["units"] * nav
        s["nav_date"] = nav_date
        s["value"] = value
        rows.append(s)

    valuation_date = max(max(latest_dates) if latest_dates else date.today(), last_tx)

    out = []
    for s in rows:
        flows = list(s["flows"])
        all_flows.extend(flows)
        xirr = calculate_xirr(flows + [(valuation_date, s["value"])]) if s["value"] > 0 else None
        gain = s["value"] - s["invested"]
        out.append({
            "Scheme": s["name"],
            "Invested": s["invested"],
            "Units": s["units"],
            "Latest NAV Date": pretty(to_iso(s["nav_date"])) if s["nav_date"] else "—",
            "Current Value": s["value"],
            "Gain / Loss": gain,
            "Absolute %": (gain / s["invested"] * 100) if s["invested"] else 0.0,
            "XIRR %": xirr * 100 if xirr is not None else None,
        })

    summary = pd.DataFrame(out)
    total_value = summary["Current Value"].sum() if not summary.empty else 0.0
    all_flows.append((valuation_date, total_value))
    return summary, all_flows, valuation_date


def portfolio_timeline(transactions, end_date):
    df = pd.DataFrame(
        transactions,
        columns=["id", "scheme_id", "code", "name", "date", "amount", "units"],
    )
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    if df.empty:
        return None

    start = df["date"].min()
    end = pd.Timestamp(end_date)
    if end < start:
        return None
    daily = pd.date_range(start, end, freq="D")

    total_invested = pd.Series(0.0, index=daily)
    total_value = pd.Series(0.0, index=daily)

    for scheme_id, g in df.groupby("scheme_id"):
        nav_df = get_nav_series(scheme_id, to_iso((start - pd.Timedelta(days=10)).date()))
        if nav_df.empty:
            continue
        nav = nav_df.set_index("nav_date")["nav"]
        nav = nav[~nav.index.duplicated()]
        nav_daily = nav.reindex(nav.index.union(daily)).ffill().reindex(daily)

        inv = g.groupby("date")["amount"].sum().reindex(daily, fill_value=0.0).cumsum()
        units = g.groupby("date")["units"].sum().reindex(daily, fill_value=0.0).cumsum()

        total_invested = total_invested.add(inv, fill_value=0.0)
        total_value = total_value.add((units * nav_daily).fillna(0.0), fill_value=0.0)

    return pd.DataFrame({"Invested": total_invested, "Value": total_value})


# ============================================================
# MAIN UI
# ============================================================

st.markdown(
    """
    <h1 style="font-size: 38px; font-weight: 700;">📈 Mutual Fund NAV Tracker &amp; SIP Return Calculator</h1>
    <p style="font-size: 20px; font-weight: 600;">NAV source: AMFI</p>
    """,
    unsafe_allow_html=True,
)

st.sidebar.title("Navigation")
page = st.sidebar.radio(
    "Go to",
    ["Dashboard", "Download NAV", "Scheme Search", "NAV Chart", "Add SIP", "SIP Returns"],
)

# ============================================================
# DASHBOARD
# ============================================================

if page == "Dashboard":
    st.header("Dashboard")

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM schemes")
    schemes_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM nav_history")
    nav_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM sip_transactions")
    transaction_count = cur.fetchone()[0]

    c1, c2, c3 = st.columns(3)
    c1.metric("Mutual Fund Schemes", f"{schemes_count:,}")
    c2.metric("NAV History Records", f"{nav_count:,}")
    c3.metric("SIP Transactions", f"{transaction_count:,}")

    st.divider()

    if nav_count == 0:
        st.info("No NAV history exists yet. Go to 'Download NAV' and import historical data.")
    else:
        cur.execute("SELECT MIN(nav_date), MAX(nav_date) FROM nav_history")
        lo, hi = cur.fetchone()
        st.subheader("NAV Database")
        st.write(f"Available NAV range: **{pretty(lo)} → {pretty(hi)}**")

        cur.execute("""
            SELECT substr(nav_date, 1, 7) AS ym, COUNT(*)
            FROM nav_history GROUP BY ym ORDER BY ym
        """)
        monthly = cur.fetchall()
        if monthly:
            mdf = pd.DataFrame(monthly, columns=["Month", "Records"])
            fig, ax = plt.subplots(figsize=(11, 3.8))
            ax.bar(mdf["Month"], mdf["Records"], color=PALETTE[0])
            step = max(1, len(mdf) // 12)
            ax.set_xticks(range(0, len(mdf), step))
            ax.set_xticklabels(mdf["Month"].iloc[::step], rotation=45, ha="right")
            ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
            style_axes(ax, "NAV Records Imported per Month", "", "Records")
            show_fig(fig)

    conn.close()

# ============================================================
# DOWNLOAD NAV
# ============================================================

elif page == "Download NAV":
    st.header("⬇️ Download NAV from AMFI")
    st.write("Download historical daily NAV data directly from AMFI.")

    col1, col2 = st.columns(2)
    start_date = col1.date_input("From date", value=date(2025, 1, 1))
    end_date = col2.date_input("To date", value=date(2025, 1, 31))

    if st.button("Download & Import NAV", type="primary"):
        if start_date > end_date:
            st.error("From date cannot be after To date.")
        else:
            with st.spinner("Downloading NAV data from AMFI..."):
                try:
                    text = download_amfi(start_date, end_date)
                    st.success(f"Downloaded {len(text):,} characters.")

                    result = import_nav_data(text)
                    st.success("NAV import completed.")

                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Parsed", f"{result['parsed']:,}")
                    c2.metric("Matched", f"{result['matched']:,}")
                    c3.metric("Inserted", f"{result['inserted']:,}")
                    c4.metric("Skipped", f"{result['skipped']:,}")

                    st.info(
                        "Schemes are matched using the AMFI scheme code, not the scheme name. "
                        "This allows scheme names to change without breaking historical records."
                    )
                except Exception as e:
                    st.error(f"Download/import failed: {e}")

# ============================================================
# SCHEME SEARCH
# ============================================================

elif page == "Scheme Search":
    st.header("🔎 Search Mutual Funds")

    search = st.text_input("Enter scheme name or AMFI code", placeholder="Example: Axis, Tata, 139619")

    if search.strip():
        results = search_schemes(search)

        if not results:
            st.warning("No matching schemes found.")
        else:
            st.write(f"Found {len(results)} matching schemes.")
            for scheme_id, code, name in results:
                with st.container(border=True):
                    st.write(f"**{name}**")
                    st.caption(f"AMFI Code: {code} | Scheme ID: {scheme_id}")
                    latest = get_latest_nav(scheme_id)
                    if latest:
                        st.write(f"Latest NAV: **₹{latest[1]:.4f}** ({pretty(latest[0])})")

# ============================================================
# NAV CHART
# ============================================================

elif page == "NAV Chart":
    st.header("📉 NAV History Chart")

    search = st.text_input("Search fund", placeholder="Example: Axis Bluechip or 120503", key="chart_search")

    if search.strip():
        results = search_schemes(search)
        if not results:
            st.warning("No schemes found. Download NAV data first.")
        else:
            options = {f"{code} — {name}": (sid, name) for sid, code, name in results}
            choice = st.selectbox("Select scheme", list(options.keys()), key="chart_scheme")
            scheme_id, scheme_name = options[choice]

            full = get_nav_series(scheme_id)
            if full.empty:
                st.warning("No NAV data stored for this scheme.")
            else:
                min_d = full["nav_date"].min().date()
                max_d = full["nav_date"].max().date()

                c1, c2 = st.columns(2)
                f = c1.date_input("From", value=min_d, min_value=min_d, max_value=max_d, key="chart_from")
                t = c2.date_input("To", value=max_d, min_value=min_d, max_value=max_d, key="chart_to")
                show_ma = st.checkbox("Show 30-day moving average", value=True)

                if f > t:
                    st.error("From date cannot be after To date.")
                else:
                    df = full[(full["nav_date"].dt.date >= f) & (full["nav_date"].dt.date <= t)].reset_index(drop=True)
                    if len(df) < 2:
                        st.info("Not enough data points in this range to draw a chart.")
                    else:
                        change = (df["nav"].iloc[-1] / df["nav"].iloc[0] - 1) * 100
                        m1, m2, m3, m4 = st.columns(4)
                        m1.metric("Latest NAV", f"₹{df['nav'].iloc[-1]:.4f}")
                        m2.metric("Highest", f"₹{df['nav'].max():.4f}")
                        m3.metric("Lowest", f"₹{df['nav'].min():.4f}")
                        m4.metric("Change in range", f"{change:+.2f}%")
                        show_fig(plot_nav_history(df, scheme_name, show_ma))

# ============================================================
# ADD SIP
# ============================================================

elif page == "Add SIP":
    st.header("💰 Add SIP Investment")
    st.write(
        "Enter an investment. The application automatically uses the NAV "
        "available on or before the transaction date."
    )

    search = st.text_input("Search fund", placeholder="Example: Tata Quant Fund")

    selected_scheme = None
    if search.strip():
        results = search_schemes(search)
        if results:
            options = {f"{code} — {name}": (sid, code, name) for sid, code, name in results}
            selected = st.selectbox("Select scheme", list(options.keys()))
            selected_scheme = options[selected]
        else:
            st.warning("No schemes found. Download NAV data first.")

    mode = st.radio("Type", ["Single investment", "Monthly SIP series"], horizontal=True)

    amount = st.number_input("Investment amount (₹)", min_value=1.0, value=5000.0, step=500.0)

    if mode == "Single investment":
        tx_date = st.date_input("Investment date", value=date.today())
        months = 1
    else:
        tx_date = st.date_input("First SIP date", value=date.today() - timedelta(days=365))
        months = st.number_input("Number of monthly instalments", min_value=1, max_value=360, value=12, step=1)

    if st.button("Add Investment", type="primary"):
        if selected_scheme is None:
            st.error("Please select a mutual fund.")
        else:
            scheme_id = selected_scheme[0]
            dates = [add_months(tx_date, i, day=tx_date.day) for i in range(int(months))]
            dates = [d for d in dates if d <= date.today()]

            if not dates:
                st.error("All instalment dates are in the future.")
            else:
                added, failed = 0, []
                for d in dates:
                    ok, units, msg = save_transaction(scheme_id, d, amount)
                    if ok:
                        added += 1
                    else:
                        failed.append(f"{pretty(to_iso(d))}: {msg}")

                if added:
                    st.success(f"{added} investment(s) added successfully.")
                if failed:
                    with st.expander(f"{len(failed)} not added", expanded=not added):
                        for f_ in failed:
                            st.write("• " + f_)

# ============================================================
# SIP RETURNS
# ============================================================

elif page == "SIP Returns":
    st.header("📊 SIP Returns & XIRR")

    transactions = get_transactions()

    if not transactions:
        st.info("No SIP transactions yet. Go to 'Add SIP' first.")
    else:
        summary, cashflows, valuation_date = build_portfolio(transactions)

        total_invested = float(summary["Invested"].sum())
        total_value = float(summary["Current Value"].sum())
        profit = total_value - total_invested
        xirr = calculate_xirr(cashflows) if total_value > 0 else None

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Invested", f"₹{total_invested:,.2f}")
        c2.metric("Current Value", f"₹{total_value:,.2f}")
        c3.metric("Profit / Loss", f"₹{profit:,.2f}",
                  delta=f"{profit / total_invested * 100:+.2f}%" if total_invested else None)
        c4.metric("XIRR (annualised)", f"{xirr * 100:.2f}%" if xirr is not None else "N/A")

        st.caption(f"Valued as of {valuation_date.strftime('%d-%b-%Y')} using the latest stored NAV.")
        if xirr is None:
            st.warning(
                "XIRR could not be calculated. Make sure NAV data exists on/after "
                "your investment dates so a current value is available."
            )

        st.divider()
        st.subheader("Holdings by Scheme")
        _stretch(
            st.dataframe,
            summary.style.format({
                "Invested": "₹{:,.2f}",
                "Units": "{:,.4f}",
                "Current Value": "₹{:,.2f}",
                "Gain / Loss": "₹{:,.2f}",
                "Absolute %": "{:+.2f}%",
                "XIRR %": lambda v: "—" if pd.isna(v) else f"{v:.2f}%",
            }),
            hide_index=True,
        )

        st.divider()
        st.subheader("📈 Charts")

        timeline = portfolio_timeline(transactions, valuation_date)
        if timeline is not None and timeline["Value"].sum() > 0:
            show_fig(plot_growth(timeline))
        else:
            st.info("Growth chart needs NAV history covering your investment dates.")

        left, right = st.columns([3, 2])
        with left:
            show_fig(plot_invested_vs_value(summary))
        with right:
            show_fig(plot_allocation(summary))

        xfig = plot_xirr_bars(summary)
        if xfig is not None:
            show_fig(xfig)

        st.divider()
        st.subheader("Your Transactions")

        tx_df = pd.DataFrame(
            [(t[0], t[3], pretty(t[4]), t[5], t[6]) for t in transactions],
            columns=["ID", "Scheme", "Date", "Invested", "Units"],
        )
        _stretch(
            st.dataframe,
            tx_df.style.format({"Invested": "₹{:,.2f}", "Units": "{:,.6f}"}),
            hide_index=True,
        )

        with st.expander("Delete a transaction"):
            del_id = st.selectbox("Transaction ID", tx_df["ID"].tolist())
            if st.button("Delete selected transaction"):
                delete_transaction(int(del_id))
                st.success(f"Transaction {del_id} deleted.")
                st.rerun()

# ============================================================
# FOOTER
# ============================================================

st.sidebar.divider()
st.sidebar.caption("Mutual Fund NAV Tracker & SIP Return Calculator")
st.sidebar.caption("NAV source: AMFI")
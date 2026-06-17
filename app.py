# ============================================================
#  Finviz AI Agent — Dashboard Streamlit
#  Pré-requis : pip install -r requirements.txt
#  Lancement  : streamlit run app.py
# ============================================================

import time
import random
from datetime import datetime

import pandas as pd
import streamlit as st
import altair as alt
import requests
import re
import json
from finvizfinance.quote import finvizfinance

# ============================================================
#  CONFIGURATION DE LA PAGE
# ============================================================
st.set_page_config(
    page_title="Finviz AI Agent",
    page_icon="📊",
    layout="wide",
)

DEFAULT_WATCHLIST = [
    "AVGO", "MRVL", "MU", "COHR", "LITE", "ALAB", "CRDO",
    "AMAT", "LRCX", "KLAC", "TER", "VRT", "GEV",
    "CEG", "VST", "NEE",
]

# Noms de clés Finviz tels que retournés par finvizfinance.
# Si une clé change dans une future version de la lib, modifier ici uniquement.
KEY_MAP = {
    "pe": "P/E",
    "fwd_pe": "Forward P/E",
    "peg": "PEG",
    "oper_margin": "Oper. Margin",
    "eps_next_y": "EPS next Y",
    "eps_next_5y": "EPS next 5Y",
    "rsi": "RSI (14)",
    "debt_eq": "Debt/Eq",
    "roe": "ROE",
    "price": "Price",
    "52w_high": "52W Range To",
    "52w_low": "52W Range From",
    "target_price": "Target Price",
    "optionable": "Optionable",
}

DIAGNOSTIC_ORDER = [
    "🟢 Value / Fort potentiel",
    "🔵 Pure croissance",
    "🟡 Standard",
    "⚠️ Surchauffe",
    "🔴 Non profitable",
]

# Même User-Agent que celui utilisé par la lib finvizfinance (a fait ses preuves sur ce site)
FINVIZ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_4) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/81.0.4044.138 Safari/537.36"
}


# ============================================================
#  LOGIQUE MÉTIER (identique au script original)
# ============================================================
def get_metric(m: dict, key: str):
    key_lower = key.strip().lower()
    for k, v in m.items():
        if k.strip().lower() == key_lower:
            return v
    return None


def clean_float(value):
    if value is None or value == '-':
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    value = str(value).replace('%', '').replace('$', '').strip()
    multipliers = {'K': 1e3, 'M': 1e6, 'B': 1e9, 'T': 1e12}
    if value and value[-1].upper() in multipliers:
        try:
            return float(value[:-1]) * multipliers[value[-1].upper()]
        except ValueError:
            return None
    try:
        return float(value)
    except ValueError:
        return None


def fetch_single(ticker: str, retries: int = 3):
    """Récupère les fondamentaux d'un ticker avec retry + pause anti-ban
    (la pause n'a lieu qu'après un vrai appel réseau, jamais sur du cache)."""
    for attempt in range(retries):
        try:
            stock = finvizfinance(ticker)
            fundament = stock.ticker_fundament()
            time.sleep(random.uniform(2.5, 4.5))
            if not fundament:
                return None, f"Données vides pour {ticker}."
            return fundament, None
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
            else:
                return None, f"Échec définitif pour {ticker} : {e}"


def fetch_put_call_oi_ratio(ticker: str, retries: int = 2):
    """
    Calcule le ratio Put/Call sur l'Open Interest directement depuis la page
    options de Finviz (échéance la plus proche, sélectionnée par défaut).

    Finviz injecte la chaîne d'options complète en JSON dans une balise
    <script id="route-init-data">, ce qui évite de parser un tableau HTML —
    on additionne simplement les champs "openInterest" par type (call/put).

    Retourne (ratio, oi_calls, oi_puts, échéance, erreur). `erreur` est None
    en cas de succès, sinon une chaîne expliquant précisément ce qui a échoué
    (HTTP, timeout, balise introuvable, etc.) pour pouvoir diagnostiquer
    sans deviner.
    """
    url = f"https://finviz.com/stock?t={ticker}&ty=oc"
    headers = {
        **FINVIZ_HEADERS,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://finviz.com/",
    }
    last_error = None

    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            time.sleep(random.uniform(2.0, 4.0))  # courtoisie anti-ban Finviz

            match = re.search(
                r'<script id="route-init-data" type="application/json">(.*?)</script>',
                resp.text, re.DOTALL,
            )
            if not match:
                return None, None, None, None, (
                    f"{ticker} : balise route-init-data introuvable "
                    f"(HTTP {resp.status_code}, {len(resp.text)} caractères reçus)"
                )

            data = json.loads(match.group(1))
            options = data.get("options", [])
            if not options:
                return None, None, None, None, f"{ticker} : JSON valide mais liste 'options' vide"

            oi_calls = sum(o.get("openInterest", 0) or 0 for o in options if o.get("type") == "call")
            oi_puts = sum(o.get("openInterest", 0) or 0 for o in options if o.get("type") == "put")
            expiry = data.get("currentExpiry")

            if oi_calls == 0:
                return None, oi_calls, oi_puts, expiry, f"{ticker} : OI calls = 0"

            ratio = round(oi_puts / oi_calls, 2)
            return ratio, oi_calls, oi_puts, expiry, None

        except requests.exceptions.HTTPError as e:
            last_error = f"{ticker} : erreur HTTP {e}"
        except requests.exceptions.Timeout:
            last_error = f"{ticker} : timeout après 10s"
        except json.JSONDecodeError as e:
            last_error = f"{ticker} : JSON invalide ({e})"
        except Exception as e:
            last_error = f"{ticker} : {type(e).__name__} - {e}"

        if attempt < retries - 1:
            time.sleep(3)

    return None, None, None, None, last_error


def score(peg, operating_margin, pe, eps_next_y, rsi):
    reasons = []

    if peg is not None:
        if 0 < peg < 1.0:
            reasons.append(f"Croissance sous-évaluée (PEG={peg:.2f})")
        elif peg > 3.0:
            reasons.append(f"Valorisation tendue (PEG={peg:.2f})")

    if operating_margin is not None:
        if operating_margin > 20.0:
            reasons.append(f"Excellente rentabilité ({operating_margin:.1f}%)")
        elif operating_margin < 0:
            reasons.append(f"Marge négative ({operating_margin:.1f}%)")

    if rsi is not None:
        if rsi > 70:
            reasons.append(f"Surachat (RSI={rsi:.1f})")
        elif rsi < 30:
            reasons.append(f"Survente (RSI={rsi:.1f})")

    peg_tendu = peg is not None and peg > 3.0
    peg_value = peg is not None and 0 < peg < 1.0
    marge_ok = operating_margin is not None and operating_margin > 15.0
    marge_neg = operating_margin is not None and operating_margin < 0
    pe_eleve = pe is not None and pe > 50
    eps_fort = eps_next_y is not None and eps_next_y > 20.0

    if marge_neg:
        rating = "🔴 Non profitable"
    elif peg_tendu:
        rating = "⚠️ Surchauffe"
    elif peg_value and marge_ok:
        rating = "🟢 Value / Fort potentiel"
    elif pe_eleve and eps_fort and not peg_tendu:
        rating = "🔵 Pure croissance"
    else:
        rating = "🟡 Standard"

    return rating, reasons


@st.cache_data(show_spinner=False, ttl=3600)
def analyze_ticker(ticker: str, _cache_buster: int):
    """_cache_buster permet d'invalider le cache via le bouton Rafraîchir
    sans toucher à la logique métier."""
    fundament, error = fetch_single(ticker)
    if fundament is None:
        return {"Ticker": ticker, "_error": error}

    gm = lambda key: get_metric(fundament, KEY_MAP[key])

    pe = clean_float(gm("pe"))
    fwd_pe = clean_float(gm("fwd_pe"))
    peg = clean_float(gm("peg"))
    operating_margin = clean_float(gm("oper_margin"))
    eps_next_y = clean_float(gm("eps_next_y"))
    eps_next_5y = clean_float(gm("eps_next_5y"))
    rsi = clean_float(gm("rsi"))
    debt_eq = clean_float(gm("debt_eq"))
    roe = clean_float(gm("roe"))
    price = clean_float(gm("price"))
    high_52w = clean_float(gm("52w_high"))
    target_price = clean_float(gm("target_price"))

    dist_52w_high = None
    if price and high_52w:
        dist_52w_high = round((price / high_52w - 1) * 100, 1)

    upside_target = None
    if price and target_price:
        upside_target = round((target_price / price - 1) * 100, 1)

    # Toujours tenté : le champ "Optionable" de Finviz s'est révélé peu fiable
    # comme filtre (faux négatifs sur des titres clairement optionables).
    pc_ratio, oi_calls, oi_puts, pc_expiry, pc_error = fetch_put_call_oi_ratio(ticker)

    rating, reasons = score(peg, operating_margin, pe, eps_next_y, rsi)

    # Enrichissement informatif du diagnostic (n'altère pas la catégorie de rating)
    if upside_target is not None:
        if upside_target > 15:
            reasons.append(f"Potentiel vs target +{upside_target:.1f}%")
        elif upside_target < -10:
            reasons.append(f"Déjà {abs(upside_target):.1f}% au-dessus du target")

    if pc_ratio is not None:
        if pc_ratio > 1.0:
            reasons.append(f"Sentiment optionnel baissier (Put/Call OI={pc_ratio:.2f})")
        elif pc_ratio < 0.7:
            reasons.append(f"Sentiment optionnel haussier (Put/Call OI={pc_ratio:.2f})")

    return {
        "Ticker": ticker,
        "Prix": price,
        "Target Price": target_price,
        "Potentiel vs Target (%)": upside_target,
        "P/E": pe,
        "Forward P/E": fwd_pe,
        "PEG": peg,
        "Marge Opé (%)": operating_margin,
        "EPS next Y (%)": eps_next_y,
        "EPS next 5Y (%)": eps_next_5y,
        "ROE (%)": roe,
        "Debt/Eq": debt_eq,
        "RSI (14)": rsi,
        "Dist. 52W High (%)": dist_52w_high,
        "Put/Call OI": pc_ratio,
        "OI Calls": oi_calls,
        "OI Puts": oi_puts,
        "Échéance Options": pc_expiry,
        "Diagnostic": rating,
        "Points clés": " | ".join(reasons) if reasons else "—",
        "_error": None,
        "_pc_error": pc_error,
    }


# ============================================================
#  ÉTAT DE SESSION
# ============================================================
st.session_state.setdefault("cache_buster", 0)
st.session_state.setdefault("df", pd.DataFrame())
st.session_state.setdefault("errors", [])
st.session_state.setdefault("options_errors", [])
st.session_state.setdefault("last_run", None)

# ============================================================
#  SIDEBAR — SAISIE & REFRESH
# ============================================================
st.sidebar.title("⚙️ Paramètres")

tickers_input = st.sidebar.text_area(
    "Tickers à analyser (séparés par des virgules)",
    value=", ".join(DEFAULT_WATCHLIST),
    height=100,
)
tickers = list(dict.fromkeys(
    [t.strip().upper() for t in tickers_input.split(",") if t.strip()]
))
st.sidebar.caption(f"{len(tickers)} valeur(s) unique(s)")

refresh = st.sidebar.button(
    "🔄 Rafraîchir les données", width="stretch", type="primary"
)

st.sidebar.divider()
st.sidebar.subheader("🔎 Filtres")
filters_placeholder = st.sidebar.container()

# ============================================================
#  COLLECTE / ANALYSE
# ============================================================
auto_run = st.session_state.df.empty and st.session_state.last_run is None
should_fetch = (refresh or auto_run) and len(tickers) > 0

if should_fetch:
    if refresh:
        st.session_state.cache_buster += 1  # invalide le cache → force un vrai refetch

    progress = st.sidebar.progress(0, text="Initialisation…")
    status = st.sidebar.empty()
    results = []

    for i, ticker in enumerate(tickers):
        status.write(f"📡 {ticker}…")
        results.append(analyze_ticker(ticker, st.session_state.cache_buster))
        progress.progress((i + 1) / len(tickers), text=f"{ticker} ✓")

    status.empty()
    progress.empty()

    raw = pd.DataFrame(results)
    st.session_state.errors = (
        raw.loc[raw["_error"].notna(), "_error"].tolist() if "_error" in raw else []
    )
    st.session_state.options_errors = (
        raw.loc[raw["_pc_error"].notna(), "_pc_error"].tolist() if "_pc_error" in raw else []
    )
    st.session_state.df = (
        raw.loc[raw["_error"].isna()].drop(columns=["_error", "_pc_error"], errors="ignore")
        if "_error" in raw else raw
    )
    st.session_state.last_run = datetime.now()

df = st.session_state.df

# ============================================================
#  EN-TÊTE
# ============================================================
st.title("📊 Finviz AI Agent")
st.caption("Analyse automatique des fondamentaux et signaux techniques via Finviz")

if st.session_state.last_run:
    st.caption(f"Dernière mise à jour : {st.session_state.last_run.strftime('%d/%m/%Y %H:%M:%S')}")

if df.empty:
    st.info("👈 Renseigne des tickers dans la barre latérale puis clique sur **Rafraîchir les données**.")
    st.stop()

# ============================================================
#  FILTRES (construits maintenant que les données existent)
# ============================================================
with filters_placeholder:
    diag_available = [d for d in DIAGNOSTIC_ORDER if d in df["Diagnostic"].unique()]
    selected_diag = st.multiselect("Diagnostic", diag_available, default=diag_available)

    peg_ceiling = float(max(10.0, df["PEG"].max(skipna=True) or 10.0))
    peg_range = st.slider("PEG", 0.0, peg_ceiling, (0.0, peg_ceiling))

    rsi_range = st.slider("RSI (14)", 0, 100, (0, 100))

    pc_ceiling = float(max(3.0, df["Put/Call OI"].max(skipna=True) or 3.0))
    pc_range = st.slider("Put/Call OI Ratio", 0.0, pc_ceiling, (0.0, pc_ceiling))

    sortable_cols = [c for c in df.columns if c not in ("Ticker", "Points clés")]
    default_sort = "Dist. 52W High (%)" if "Dist. 52W High (%)" in sortable_cols else sortable_cols[0]
    sort_col = st.selectbox("Trier par", sortable_cols, index=sortable_cols.index(default_sort))
    sort_asc = st.checkbox("Ordre croissant", value=True)

mask = df["Diagnostic"].isin(selected_diag)
mask &= df["PEG"].isna() | df["PEG"].between(*peg_range)
mask &= df["RSI (14)"].isna() | df["RSI (14)"].between(*rsi_range)
mask &= df["Put/Call OI"].isna() | df["Put/Call OI"].between(*pc_range)
filtered = df.loc[mask].sort_values(sort_col, ascending=sort_asc, na_position="last")

# ============================================================
#  RÉSUMÉ CHIFFRÉ
# ============================================================
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Analysées", len(df))
c2.metric("🟢 Value", int((df["Diagnostic"] == "🟢 Value / Fort potentiel").sum()))
c3.metric("🔵 Croissance", int((df["Diagnostic"] == "🔵 Pure croissance").sum()))
c4.metric("⚠️ Surchauffe", int((df["Diagnostic"] == "⚠️ Surchauffe").sum()))
c5.metric("🔴 Non profitable", int((df["Diagnostic"] == "🔴 Non profitable").sum()))

# ============================================================
#  TABLEAU
# ============================================================
st.subheader(f"Résultats ({len(filtered)} valeur(s) après filtres)")

st.dataframe(
    filtered,
    width="stretch",
    hide_index=True,
    column_config={
        "Prix": st.column_config.NumberColumn(format="$%.2f"),
        "Target Price": st.column_config.NumberColumn(format="$%.2f"),
        "Potentiel vs Target (%)": st.column_config.NumberColumn(format="%.1f%%"),
        "P/E": st.column_config.NumberColumn(format="%.1f"),
        "Forward P/E": st.column_config.NumberColumn(format="%.1f"),
        "PEG": st.column_config.NumberColumn(format="%.2f"),
        "Marge Opé (%)": st.column_config.NumberColumn(format="%.1f%%"),
        "EPS next Y (%)": st.column_config.NumberColumn(format="%.1f%%"),
        "EPS next 5Y (%)": st.column_config.NumberColumn(format="%.1f%%"),
        "ROE (%)": st.column_config.NumberColumn(format="%.1f%%"),
        "Debt/Eq": st.column_config.NumberColumn(format="%.2f"),
        "RSI (14)": st.column_config.NumberColumn(format="%.1f"),
        "Dist. 52W High (%)": st.column_config.NumberColumn(format="%.1f%%"),
        "Put/Call OI": st.column_config.NumberColumn(format="%.2f"),
        "OI Calls": st.column_config.NumberColumn(format="%d"),
        "OI Puts": st.column_config.NumberColumn(format="%d"),
    },
)

csv_bytes = filtered.to_csv(index=False).encode("utf-8-sig")
st.download_button(
    "💾 Télécharger en CSV", csv_bytes,
    file_name="rapport_finviz.csv", mime="text/csv",
)

# ============================================================
#  GRAPHIQUE — PEG vs croissance EPS
# ============================================================
chart_data = filtered.dropna(subset=["PEG", "EPS next Y (%)"])
if not chart_data.empty:
    st.subheader("PEG vs croissance EPS estimée (N+1)")
    chart = (
        alt.Chart(chart_data)
        .mark_circle(size=140, opacity=0.8)
        .encode(
            x=alt.X("PEG", title="PEG Ratio"),
            y=alt.Y("EPS next Y (%)", title="Croissance EPS estimée (%)"),
            color=alt.Color("Diagnostic", title="Diagnostic"),
            tooltip=["Ticker", "PEG", "EPS next Y (%)", "Forward P/E", "Diagnostic"],
        )
        .interactive()
    )
    st.altair_chart(chart, width="stretch")

# ============================================================
#  ERREURS DE RÉCUPÉRATION
# ============================================================
if st.session_state.errors:
    with st.expander(f"⚠️ {len(st.session_state.errors)} erreur(s) de récupération (fondamentaux Finviz)"):
        for err in st.session_state.errors:
            st.write(f"- {err}")

if st.session_state.options_errors:
    with st.expander(f"⚠️ {len(st.session_state.options_errors)} erreur(s) Put/Call OI (page options Finviz)"):
        for err in st.session_state.options_errors:
            st.write(f"- {err}")

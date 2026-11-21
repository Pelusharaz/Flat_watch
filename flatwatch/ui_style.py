"""Visual design system for the Streamlit UI: CSS + small HTML components.
All user/listing text passed to these helpers is HTML-escaped here."""
from __future__ import annotations

import html as _html

BRAND = "#e4572e"

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
:root{--brand:#e4572e;--brand2:#ff8a5b;--ink:#111827;--muted:#6b7280;--line:#eceef3;--bg:#f6f7fb;
--ok:#16a34a;--okbg:#dcfce7;--warn:#d97706;--warnbg:#fef3c7;--bad:#dc2626;--badbg:#fee2e2;--info:#2563eb;--infobg:#dbeafe}
html,body,.stApp,[class*="css"]{font-family:'Inter',-apple-system,'Segoe UI',Roboto,sans-serif}
.stApp{background:var(--bg)}
.block-container{padding-top:2.2rem;max-width:1220px}
h1,h2,h3{color:var(--ink);letter-spacing:-.02em;font-weight:800}
[data-testid="stSidebar"]{background:#fff;border-right:1px solid var(--line)}
[data-testid="stSidebarNav"] a{border-radius:10px}
.stButton>button,.stDownloadButton>button,.stFormSubmitButton>button,.stLinkButton>a{border-radius:10px;font-weight:600}
.stButton>button[kind="primary"],.stFormSubmitButton>button[kind="primary"]{background:linear-gradient(135deg,var(--brand2),var(--brand));border:0}
[data-testid="stVerticalBlockBorderWrapper"]{border-radius:18px}
div[data-testid="stVerticalBlockBorderWrapper"]:has(> div > div > div > .fw-card-marker){background:#fff;box-shadow:0 6px 24px rgba(17,24,39,.06);border-color:#f0f1f5}
[data-testid="stMetric"]{background:#fff;border:1px solid var(--line);border-radius:16px;padding:14px 18px;box-shadow:0 2px 10px rgba(17,24,39,.04)}
[data-testid="stMetricValue"]{font-weight:800}
[data-testid="stImage"] img{border-radius:14px}
[data-testid="stExpander"]{background:#fff;border-radius:14px}
[data-testid="stChatMessage"]{background:#fff;border:1px solid var(--line);border-radius:16px;padding:10px 14px}
.st-key-hp_website{display:none!important}

.fw-hero{background:linear-gradient(120deg,#ff8a5b 0%,#e4572e 45%,#7c3aed 120%);color:#fff;border-radius:26px;
padding:38px 40px;margin-bottom:18px;box-shadow:0 18px 40px rgba(228,87,46,.25)}
.fw-hero h1{color:#fff;font-size:2.4rem;margin:0 0 6px 0}
.fw-hero p{font-size:1.08rem;opacity:.95;margin:0}
.fw-features{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:18px 0}
.fw-feature{background:#fff;border-radius:18px;padding:18px;border:1px solid var(--line)}
.fw-feature b{display:block;font-size:1.02rem;margin:6px 0 4px}
.fw-feature span{color:var(--muted);font-size:.92rem}
.fw-page-sub{color:var(--muted);margin-top:-8px;margin-bottom:18px}

.fw-title{font-size:1.15rem;font-weight:700;color:var(--ink);line-height:1.3;margin:0 0 6px}
.fw-sub{color:var(--muted);font-size:.9rem;margin-bottom:8px}
.fw-chip{display:inline-block;background:#f3f4f6;color:#374151;border-radius:999px;padding:3px 10px;margin:0 6px 6px 0;font-size:.8rem;font-weight:500}
.fw-chip.ok{background:var(--okbg);color:#166534}.fw-chip.warn{background:var(--warnbg);color:#92400e}
.fw-chip.bad{background:var(--badbg);color:#991b1b}.fw-chip.info{background:var(--infobg);color:#1e40af}
.fw-badge{display:inline-block;border-radius:999px;padding:4px 12px;font-weight:700;font-size:.82rem}
.fw-price{font-size:1.35rem;font-weight:800;color:var(--ink)}
.fw-price small{font-size:.8rem;color:var(--muted);font-weight:500}
.fw-row{display:flex;gap:16px;align-items:center;justify-content:space-between}
.fw-ring{--p:0;--c:#16a34a;width:66px;height:66px;border-radius:50%;flex:none;
background:conic-gradient(var(--c) calc(var(--p)*1%),#e5e7eb 0);display:grid;place-items:center}
.fw-ring span{width:52px;height:52px;border-radius:50%;background:#fff;display:grid;place-items:center;font-weight:800;font-size:.95rem;color:var(--ink)}
.fw-bars{margin-top:6px}
.fw-bar{display:grid;grid-template-columns:130px 1fr 44px;gap:10px;align-items:center;font-size:.85rem;margin:6px 0;color:#374151}
.fw-bar .track{height:8px;background:#eef0f4;border-radius:99px;overflow:hidden}
.fw-bar .fill{height:100%;border-radius:99px}
.fw-bar .det{grid-column:2/4;color:var(--muted);font-size:.78rem;margin-top:-4px}
.fw-list{margin:4px 0 0 0;padding-left:18px;font-size:.9rem;color:#374151}
.fw-noimg{height:190px;border-radius:14px;background:repeating-linear-gradient(45deg,#f3f4f6,#f3f4f6 12px,#eceef2 12px,#eceef2 24px);
display:grid;place-items:center;color:#9ca3af;font-weight:600}
.fw-stat{background:#fff;border:1px solid var(--line);border-radius:16px;padding:14px 16px}
.fw-empty{background:#fff;border:1px dashed #d1d5db;border-radius:18px;padding:28px;text-align:center;color:var(--muted)}
.fw-conv{padding:10px 12px;border-radius:12px;margin-bottom:6px;background:#fff;border:1px solid var(--line)}
.fw-small{font-size:.8rem;color:var(--muted)}
@media (max-width: 760px){.fw-features{grid-template-columns:1fr}.fw-hero{padding:26px}.fw-hero h1{font-size:1.8rem}
.fw-bar{grid-template-columns:100px 1fr 40px}}
</style>
"""


def esc(x) -> str:
    return _html.escape(str(x if x is not None else ""))


def score_color(score: int, decision: str = "alert") -> str:
    if decision == "blocked":
        return "#dc2626"
    if decision == "skip":
        return "#9ca3af"
    return "#16a34a" if score >= 85 else "#f59e0b" if score >= 65 else "#ef4444"


def ring(score: int, decision: str = "alert") -> str:
    label = "⛔" if decision == "blocked" else f"{score}%"
    return f'<div class="fw-ring" style="--p:{score if decision != "blocked" else 100};--c:{score_color(score, decision)}"><span>{label}</span></div>'


def chip(text: str, kind: str = "") -> str:
    return f'<span class="fw-chip {kind}">{esc(text)}</span>'


def bars(criteria, limit: int = 99) -> str:
    rows = []
    for c in [c for c in criteria if c.applicable][:limit]:
        col = "#16a34a" if c.score >= 85 else "#f59e0b" if c.score >= 60 else "#ef4444"
        q = " ❔" if c.missing else ""
        rows.append(f'<div class="fw-bar"><div>{esc(c.name)}{q}</div><div class="track"><div class="fill" '
                    f'style="width:{c.score}%;background:{col}"></div></div><div><b>{c.score}%</b></div>'
                    f'<div class="det">{esc(c.detail)}</div></div>')
    return '<div class="fw-bars">' + "".join(rows) + "</div>"


def ul(items) -> str:
    return '<ul class="fw-list">' + "".join(f"<li>{esc(i)}</li>" for i in items) + "</ul>"


def hero(title: str, subtitle: str) -> str:
    return f'<div class="fw-hero"><h1>{esc(title)}</h1><p>{esc(subtitle)}</p></div>'


def features(items: list[tuple[str, str, str]]) -> str:
    return '<div class="fw-features">' + "".join(
        f'<div class="fw-feature">{icon}<b>{esc(t)}</b><span>{esc(d)}</span></div>' for icon, t, d in items) + "</div>"


NO_IMAGE = '<div class="fw-noimg">📷 No photos yet</div>'
CARD_MARKER = '<span class="fw-card-marker"></span>'

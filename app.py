"""Flat-Watch Debrecen – secure, time-saving rentals.   Run:  python -m streamlit run app.py"""
from __future__ import annotations

import time
from datetime import date, datetime

import streamlit as st
import streamlit.components.v1 as components

from flatwatch import antispam, auth, devtools, listings, mailserver, messaging, photos, searches, transit
from flatwatch import flatmates as fm
from flatwatch.bootstrap import DEMO_PASSWORD, bootstrap
from flatwatch.commute import (add_destination, commute_table, destination_maps_url, destinations,
                               maps_url, match_district)
from flatwatch.config import settings
from flatwatch.db import get_db
from flatwatch.describe import describe
from flatwatch.models import (AMENITIES, MODE_LABEL, OFFER_ICON, OFFER_LABEL, PHOTO_CATEGORIES, ROLE_LABEL,
                              Criteria, Evaluation, RawListing, profile_from_criteria)
from flatwatch.scoring import apply_feedback, config as scoring_config
from flatwatch.security import link_report, md_escape
from flatwatch.tenants import invite_message, tenants_for_offer
from flatwatch.ui_style import CARD_MARKER, CSS, NO_IMAGE, bars, chip, esc, features, hero, ring, ul
from flatwatch.worker import Worker, run_cycle

st.set_page_config(page_title="Flat-Watch Debrecen", page_icon="🏠", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)

TYPES = ["apartment", "room", "bed"]
GENDERS, LANGS = fm.GENDERS, fm.LANGUAGES
LOCAL_MAIL = settings.local_mailserver and settings.smtp_host in ("localhost", "127.0.0.1")
DEV_INBOX = LOCAL_MAIL or not settings.smtp_enabled


@st.cache_resource
def boot():
    db = get_db()
    if LOCAL_MAIL:
        mailserver.start("127.0.0.1", settings.smtp_port)
    bootstrap(db)
    w = Worker(db)
    if settings.worker_in_app:
        w.start()
    return db, w


db, worker = boot()
DEST = destinations()


def huf(n) -> str:
    return "?" if n is None else f"{n:,}".replace(",", " ") + " Ft"


def listing_external_url(l: RawListing) -> str:
    url = (l.url or "").strip()
    if l.sample or not url:
        return ""
    low = url.lower()
    if "/demo-" in low or "demo-" in low:
        return ""
    if low.startswith(("http://", "https://")):
        return url
    return ""


def ago(ts) -> str:
    if not ts:
        return "never"
    s = time.time() - ts
    return "just now" if s < 60 else f"{int(s // 60)} min ago" if s < 3600 else \
        f"{int(s // 3600)} h ago" if s < 86400 else f"{int(s // 86400)} days ago"


def short(label: str) -> str:
    return label.split(" (")[0]


def html(s: str) -> None:
    st.markdown(s, unsafe_allow_html=True)


def img(path: str, caption: str | None = None) -> None:
    """Full-width image on any Streamlit version (newer ones deprecate use_container_width)."""
    try:
        st.image(path, caption=caption, width="stretch")
    except Exception:  # noqa: BLE001 - older Streamlit: width must be an int
        st.image(path, caption=caption, use_container_width=True)


def page_header(title: str, sub: str = "") -> None:
    st.title(title)
    if sub:
        html(f'<div class="fw-page-sub">{esc(sub)}</div>')


def empty(text: str) -> None:
    html(f'<div class="fw-empty">{esc(text)}</div>')


# ===================================================== public one-click links ==
qp = st.query_params
if "unsubscribe" in qp:
    ok = searches.unsubscribe(db, qp["unsubscribe"])
    html(hero("Flat-Watch Debrecen", "Email preferences"))
    st.success("You're unsubscribed from match emails." if ok else "This link is not valid.")
    st.stop()
if "reconfirm" in qp:
    ok = listings.reconfirm(db, token=qp["reconfirm"])
    html(hero("Flat-Watch Debrecen", "Listing confirmation"))
    st.success("Thanks! Your listing is confirmed as available." if ok else "This link is no longer valid.")
    st.stop()


# ============================================================ test inbox view ==
def inbox_view(default_to: str = "") -> None:
    st.caption("Development mail server: every email the platform sends is caught here instead of going to the "
               "internet. In production, set SMTP_HOST to a real provider." if LOCAL_MAIL else
               "No SMTP configured: emails are saved as files in data/outbox/.")
    c1, c2, c3 = st.columns([3, 1, 1])
    to = c1.text_input("Show emails for", default_to, placeholder="everyone")
    if c2.button("🔄 Refresh", use_container_width=True):
        st.rerun()
    if c3.button("🗑 Clear inbox", use_container_width=True):
        mailserver.clear()
        st.rerun()
    if LOCAL_MAIL:
        mails = mailserver.inbox(to.strip() or None)
    else:
        from flatwatch.mailer import OUTBOX
        mails = [{"subject": f.name, "to": "", "date": f.stat().st_mtime, "html": f.read_text(encoding="utf-8"),
                  "from": ""} for f in sorted(OUTBOX.glob("*.html"), reverse=True)] if OUTBOX.exists() else []
    if not mails:
        empty("No emails yet. Save a search or use “Simulate a new matching listing”.")
        return
    st.markdown(f"**{len(mails)}** email(s)")
    for i, m in enumerate(mails[:40]):
        with st.expander(f"✉️ {m['subject']}  ·  {m['to']}  ·  {datetime.fromtimestamp(m['date']):%d %b %H:%M}",
                         expanded=i == 0):
            components.html(m["html"], height=520, scrolling=True)


# ================================================================== auth gate ==
user = auth.session_user(db, st.session_state.get("token"))


def auth_screen() -> None:
    html(hero("🏠 Flat-Watch Debrecen",
              "Find a flat, room or bed that really fits – or the right tenant for yours. Safely, and without "
              "spending your evenings refreshing listing sites."))
    left, right = st.columns([1.15, 1], gap="large")
    with left:
        html(features([
            ("🎯", "Explained matches", "Budget, commute, move-in date and flatmates – every score shows why."),
            ("🔔", "It keeps searching", "Save a search once. New places that fit arrive in your inbox."),
            ("🛡️", "Built against scams", "Advance-payment tricks, reposts and fake prices are flagged or blocked."),
            ("💬", "Private chat", "Talk to landlords and future flatmates without sharing your phone number."),
            ("🚋", "Real commute", "Walk, bike and the exact tram/bus line to your campus."),
            ("🏠", "For landlords too", "Post once with photos – see only tenants who fit."),
        ]))
        if settings.include_samples:
            with st.container(border=True):
                st.markdown("**🧪 Demo accounts** · password `" + DEMO_PASSWORD + "`")
                st.markdown("| Account | Role |\n|---|---|\n| `anna@demo.flatwatch.hu` | Tenant |\n"
                            "| `gabor@demo.flatwatch.hu`, `eva@…`, `peter@…` | Owners |\n"
                            "| `yasmin@…`, `rahul@…` | Tenants subletting a room / bed |\n"
                            "| `admin@demo.flatwatch.hu` | Moderator |")
    with right:
        with st.container(border=True):
            t_login, t_reg, t_verify, t_reset = st.tabs(["Log in", "Sign up", "Verify email", "Forgot password"])
            with t_login:
                with st.form("login"):
                    email = st.text_input("Email", placeholder="you@example.com")
                    pw = st.text_input("Password", type="password")
                    totp = st.text_input("Authenticator code (only if 2FA is on)", max_chars=6)
                    if st.form_submit_button("Log in", type="primary", use_container_width=True):
                        ok, msg, token, _ = auth.login(db, email, pw, totp)
                        if ok:
                            st.session_state["token"] = token
                            st.rerun()
                        st.error(msg)
            with t_reg:
                st.session_state.setdefault("reg_shown_at", time.time())
                with st.form("register"):
                    role = st.radio("I am", ["tenant", "landlord"], horizontal=True,
                                    format_func=lambda r: "🔎 Looking for a place" if r == "tenant" else "🏠 Renting out")
                    name = st.text_input("Name")
                    email = st.text_input("Email ", placeholder="you@example.com")
                    pw = st.text_input("Password", type="password", help="10+ characters with letters and numbers")
                    hp = st.text_input("Leave this field empty", key="hp_website")   # honeypot – hidden by CSS; bots fill it
                    agree = st.checkbox("I agree to the processing of my data to provide the service")
                    if st.form_submit_button("Create account", type="primary", use_container_width=True):
                        if not agree:
                            st.error("Please accept the privacy terms.")
                        else:
                            ok, msg, code = auth.register(db, email, name, pw, role, honeypot=hp,
                                                          form_seconds=time.time() - st.session_state["reg_shown_at"])
                            (st.success if ok else st.error)(msg)
                            if ok:
                                st.session_state["pending_email"] = email
                            if code:
                                st.info(f"🧪 Dev mode: your code is **{code}** (also in the 📬 test inbox below). "
                                        "Enter it in the *Verify email* tab.")
            with t_verify:
                with st.form("verify"):
                    email = st.text_input("Email  ", st.session_state.get("pending_email", ""))
                    code = st.text_input("6-digit code", max_chars=6)
                    a, b = st.columns(2)
                    if a.form_submit_button("Verify", type="primary", use_container_width=True):
                        ok, msg = auth.verify_email(db, email, code)
                        (st.success if ok else st.error)(msg)
                    if b.form_submit_button("Send a new code", use_container_width=True):
                        dev = auth.resend_verification(db, email)
                        st.info("If the account exists and isn't verified, a new code is on its way.")
                        if dev:
                            st.info(f"🧪 Dev mode code: **{dev}**")
            with t_reset:
                with st.form("reset_req"):
                    email = st.text_input("Your email")
                    if st.form_submit_button("Send reset code", use_container_width=True):
                        msg, dev = auth.request_reset(db, email)
                        st.info(msg)
                        if dev:
                            st.info(f"🧪 Dev mode code: **{dev}**")
                with st.form("reset_do"):
                    email = st.text_input("Email   ")
                    code = st.text_input("Reset code", max_chars=6)
                    pw = st.text_input("New password", type="password")
                    if st.form_submit_button("Change password", use_container_width=True):
                        ok, msg = auth.reset_password(db, email, code, pw)
                        (st.success if ok else st.error)(msg)
    if DEV_INBOX:
        with st.expander("📬 Test inbox (development) – see verification codes and alerts"):
            inbox_view(st.session_state.get("pending_email", ""))


if not user:
    auth_screen()
    st.stop()


# ================================================================ shared data ==
@st.cache_data(ttl=60, show_spinner="Matching places to your profile…")
def ranked(criteria_json: str, version: tuple) -> list[Evaluation]:
    return searches.rank(db, Criteria.model_validate_json(criteria_json), with_message=True)


def listings_version() -> tuple:
    r = db.one("SELECT COUNT(*) n, MAX(updated_at) u, MAX(created_at) c FROM listings WHERE status='active'")
    return (r["n"], r["u"], r["c"], db.one("SELECT COUNT(*) n FROM photos")["n"])


def my_profile() -> Criteria:
    return searches.get_profile(db, user.id)


def my_evaluations() -> list[Evaluation]:
    return ranked(my_profile().model_dump_json(), listings_version())


def favorite_ids() -> set[str]:
    return {r["listing_id"] for r in db.query("SELECT listing_id FROM favorites WHERE user_id=?", (user.id,))}


def go(page_key: str) -> None:
    st.switch_page(PAGES[page_key])


def open_chat(cid: int) -> None:
    st.session_state["open_conv"] = cid
    go("messages")


def commute_md(commutes: dict, highlight: str | None = None) -> str:
    rows = ["| To | 🚶 Walk | 🚲 Bike | 🚋 Bus/tram | Best public transport |", "|---|---:|---:|---:|---|"]
    for key, d in DEST.items():
        t = commutes.get(key)
        if t:
            name = f"**{short(d['label'])} ← you**" if key == highlight else short(d["label"])
            rows.append(f"| {name} | {t['walk']} min | {t.get('bike', '?')} min | {t['transit']} min | {t.get('how', '')} |")
    return "\n".join(rows)


# ================================================================== listing UI ==
def contact(ev: Evaluation, key: str, primary: bool = True) -> None:
    r = listings.row(db, ev.listing.id)
    if r and r["owner_id"] and ev.decision != "blocked":
        if st.button("💬 Message landlord", key=f"msg_{key}", type="primary" if primary else "secondary",
                     use_container_width=True):
            cid, err = messaging.start_conversation(db, user, ev.listing.id)
            if err:
                st.error(err)
            else:
                if not db.one("SELECT 1 FROM messages WHERE conversation_id=?", (cid,)) and ev.message_hu:
                    messaging.send(db, user, cid, ev.message_hu)
                open_chat(cid)
    else:
        external_url = listing_external_url(ev.listing)
        if external_url:
            st.link_button(f"🔗 Open on {ev.listing.source}", external_url, width="stretch")
        elif ev.listing.poster_role == "site":
            st.caption("From another website – contact via the original ad")


@st.dialog("Listing details", width="large")
def details(listing_id: str) -> None:
    l = listings.get(db, listing_id)
    r = listings.row(db, listing_id)
    if not l or r["status"] != "active":
        st.warning("This listing is no longer available.")
        return
    ev = next((e for e in my_evaluations() if e.listing.id == listing_id), None) if user.role == "tenant" else None
    offer = l.offer_type or (ev.details.offer_type if ev else None) or "apartment"
    html(f'<div class="fw-row"><div><div class="fw-title">{OFFER_ICON[offer]} {esc(l.title)}</div>'
         f'<div class="fw-sub">{esc(l.location)} · {esc(ROLE_LABEL[l.poster_role])} · listed {ago(r["created_at"])} · '
         f'availability confirmed {ago(r["last_verified_at"])}</div></div>'
         + (ring(ev.match_score, ev.decision) if ev else "") + "</div>")
    rows = photos.list_photos(db, listing_id)
    if rows:
        cats = ["All"] + list(dict.fromkeys(p["category"] for p in rows))
        cat = st.radio("Photos", cats, horizontal=True, label_visibility="collapsed", key=f"gcat_{listing_id}")
        shown = [p for p in rows if cat == "All" or p["category"] == cat]
        idx = st.select_slider("Photo", options=list(range(1, len(shown) + 1)), key=f"gidx_{listing_id}",
                               label_visibility="collapsed") if len(shown) > 1 else 1
        p = shown[min(idx, len(shown)) - 1]
        img(photos.abs_path(p["path"]), caption=f"{p['category']} · {idx}/{len(shown)}")
    else:
        html(NO_IMAGE)
    if ev:
        if ev.scam.flags:
            st.error(f"🛡 Scam check {ev.scam.level.upper()}: {ev.scam.explanation}")
        a, b = st.columns([1.2, 1])
        with a:
            st.markdown("#### Why this score")
            html(bars(ev.criteria_scores))
        with b:
            if ev.positives:
                st.markdown("**👍 Fits because**")
                html(ul(ev.positives))
            if ev.hard_fails or ev.negatives:
                st.markdown("**👎 Lowers the match**")
                html(ul(ev.hard_fails + ev.negatives))
            if ev.missing:
                st.markdown("**❔ Not stated in the ad**")
                html(ul(ev.missing))
    st.markdown("#### Description")
    st.write(l.description)
    hist = listings.price_history(db, listing_id)
    if len(hist) > 1:
        st.caption("Price history: " + " → ".join(f"{huf(h['price_huf'])} ({datetime.fromtimestamp(h['changed_at']):%d %b})" for h in hist))
    commutes, basis, near = commute_table(l.location, match_district(l.location))
    place_url = maps_url(l.location, match_district(l.location))
    if place_url:
        st.link_button("Open location in Google Maps", place_url)
    if near or commutes:
        st.markdown("#### Getting around")
        if near:
            st.markdown("  \n".join(f"🚏 **{s['name']}** – {s['walk_min']} min walk · {', '.join(s['lines'][:8])}" for s in near))
        if commutes:
            st.markdown(commute_md(commutes, my_profile().campus if user.role == "tenant" else None))
            st.caption(f"From {basis}. Estimates, not timetables.")
    if ev and ev.message_hu and ev.decision != "blocked":
        st.markdown(f"#### ✉️ Suggested first message ({'Hungarian' if ev.message_lang == 'hu' else 'English'})")
        st.code(ev.message_hu, language=None, wrap_lines=True)
        contact(ev, "dlg")
    with st.popover("🚩 Report this listing"):
        reason = st.text_area("What's wrong?", key=f"rep_{listing_id}")
        if st.button("Send report", key=f"reps_{listing_id}"):
            messaging.report(db, user, reason or "suspicious listing", target_user_id=r["owner_id"], listing_id=listing_id)
            st.success("Thanks – a moderator will review it.")


def card(ev: Evaluation, key: str, favs: set[str], compact: bool = False) -> None:
    l, d = ev.listing, ev.details
    offer = d.offer_type or "apartment"
    with st.container(border=True):
        html(CARD_MARKER)
        pic, body = st.columns([1, 1.7] if not compact else [1, 1.3], gap="medium")
        with pic:
            thumb = photos.first_thumb(db, l.id)
            if thumb:
                img(photos.abs_path(thumb))
            else:
                html(NO_IMAGE)
        with body:
            status = {"alert": chip("Great match", "ok"), "maybe": chip("Worth a look", "warn"),
                      "skip": chip("Excluded" if ev.hard_fails else "Low match", ""),
                      "blocked": chip("Likely scam", "bad")}[ev.decision]
            commute_kind = {"Near": "ok", "Average": "info", "Far": "warn"}.get(ev.commute_category, "bad")
            chips = (chip(f"{OFFER_ICON[offer]} {OFFER_LABEL[offer]}") + chip(f"📍 {d.neighborhood or l.location}")
                     + chip(f"🚋 {ev.commute_min if ev.commute_min is not None else '?'} min · {ev.commute_category}", commute_kind)
                     + (chip(f"📐 {l.size_m2} m²") if l.size_m2 else "")
                     + (chip("✅ Landlord on Flat-Watch", "info") if l.poster_role != "site" else chip("🌐 " + l.source))
                     + (chip("🧪 sample") if l.sample else ""))
            html(f'<div class="fw-row"><div>{status}<div class="fw-title">{esc(l.title)}</div></div>{ring(ev.match_score, ev.decision)}</div>'
                 f'<div class="fw-price">{huf(ev.total_monthly_huf)} <small>/ month incl. utilities</small></div>'
                 f'<div style="margin-top:8px">{chips}</div>')
            if not compact:
                reasons = (ev.hard_fails or ev.positives[:2] + ev.negatives[:1]) if ev.decision != "blocked" else ev.reasons
                html(ul(reasons[:3]))
            b1, b2, b3, b4 = st.columns(4)
            if b1.button("🔍 Details", key=f"det_{key}", use_container_width=True):
                details(l.id)
            fav = l.id in favs
            if b2.button("★ Saved" if fav else "☆ Save", key=f"fav_{key}", use_container_width=True):
                if fav:
                    db.execute("DELETE FROM favorites WHERE user_id=? AND listing_id=?", (user.id, l.id))
                else:
                    db.execute("INSERT OR IGNORE INTO favorites(user_id, listing_id, created_at) VALUES(?,?,?)",
                               (user.id, l.id, time.time()))
                st.rerun()
            with b3.popover("👍 / 👎", use_container_width=True):
                verdict = st.radio("Verdict", ["like", "dislike"], horizontal=True, key=f"v_{key}",
                                   format_func=lambda v: "👍 Like" if v == "like" else "👎 Not for me")
                reason = st.selectbox("Main reason", ["budget", "commute", "move_in", "size_rooms", "furnishing",
                                                      "pets", "smoking", "amenities", "photos / looks", "other"], key=f"r_{key}")
                if st.button("Send", key=f"fb_{key}"):
                    db.execute("INSERT INTO feedback(user_id, listing_id, verdict, reason, created_at) VALUES(?,?,?,?,?)",
                               (user.id, l.id, verdict, reason, time.time()))
                    if verdict == "dislike":
                        searches.save_profile(db, user.id, apply_feedback(my_profile(), reason))
                    st.success("Thanks – your next matches will reflect this.")
            with b4:
                contact(ev, key, primary=ev.decision == "alert")


# =================================================================== TENANT ==
def deep_link() -> None:
    if "listing" in qp:                                     # link from an email: open the listing
        lid = qp["listing"]
        del st.query_params["listing"]
        details(lid)


def t_home() -> None:
    deep_link()
    c = my_profile()
    evs = my_evaluations()
    good = [e for e in evs if e.decision == "alert"]
    week = time.time() - 7 * 86400
    created = {r["id"]: r["created_at"] for r in db.query("SELECT id, created_at FROM listings")}
    new = [e for e in evs if e.decision in ("alert", "maybe") and created.get(e.listing.id, 0) > week]
    hour = datetime.now().hour
    greet = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
    html(hero(f"{greet}, {user.name.split()[0]} 👋",
              f"{len(good)} great match(es) for you · up to {huf(c.max_total_huf)}/month · "
              f"{c.max_commute_min} min {MODE_LABEL[c.commute_mode]} to {short(DEST.get(c.campus, {}).get('label', c.campus))}"))
    m = st.columns(4)
    m[0].metric("Great matches", len(good))
    m[1].metric("New this week", len(new))
    m[2].metric("Unread messages", messaging.unread_count(db, user))
    m[3].metric("Saved places", len(favorite_ids()))
    s = searches.user_searches(db, user.id)
    active = [x for x in s if x["active"]]
    if not active:
        st.info("🔔 You have no active saved search – we can't email you new places yet.")
        if st.button("Create a saved search", type="primary"):
            go("search")
    else:
        last = db.kv_get("worker_last_run")
        st.success(f"🔔 {len(active)} saved search(es) active – we check for new places every "
                   f"{settings.worker_interval_s}s (last check {ago(last['at']) if last else 'pending'}).")
    st.subheader("⭐ Top picks for you")
    favs = favorite_ids()
    top = [e for e in evs if e.decision in ("alert", "maybe")][:3]
    if not top:
        empty("No matches yet – try widening your budget or commute in Search & alerts.")
    cols = st.columns(3)
    for i, e in enumerate(top):
        with cols[i]:
            with st.container(border=True):
                html(CARD_MARKER)
                thumb = photos.first_thumb(db, e.listing.id)
                img(photos.abs_path(thumb)) if thumb else html(NO_IMAGE)
                html(f'<div class="fw-row"><div class="fw-title">{esc(e.listing.title)}</div>{ring(e.match_score, e.decision)}</div>'
                     f'<div class="fw-price">{huf(e.total_monthly_huf)} <small>/ month</small></div>'
                     f'<div class="fw-sub">{esc(e.details.neighborhood or e.listing.location)} · {e.commute_min} min · {e.commute_category}</div>')
                if st.button("🔍 Details", key=f"home_{i}", use_container_width=True):
                    details(e.listing.id)
    if st.button("See all matches →"):
        go("matches")


def t_matches() -> None:
    c = my_profile()
    page_header("🔎 Places for you", f"Ranked for {c.user_name}: budget {huf(c.max_total_huf)}, "
                f"{c.max_commute_min} min {MODE_LABEL[c.commute_mode]} to {short(DEST.get(c.campus, {}).get('label', c.campus))}.")
    deep_link()
    evs = my_evaluations()
    with st.container(border=True):
        f1, f2, f3, f4 = st.columns([1.3, 1.3, 1, 1])
        types = f1.multiselect("Type", TYPES, c.wanted_types, format_func=lambda t: f"{OFFER_ICON[t]} {OFFER_LABEL[t]}")
        show = f2.multiselect("Show", ["alert", "maybe", "skip", "blocked"], ["alert", "maybe"],
                              format_func=lambda x: {"alert": "✅ Great", "maybe": "🟡 Maybe", "skip": "❌ Excluded",
                                                     "blocked": "⛔ Scams"}[x])
        sort = f3.selectbox("Sort", ["Best match", "Lowest price", "Shortest commute", "Newest"])
        maxp = f4.number_input("Max Ft/month", 0, 1_000_000, 0, step=10_000, help="0 = your profile budget")
    evs = [e for e in evs if (e.details.offer_type or "apartment") in types and e.decision in show
           and (not maxp or e.total_monthly_huf <= maxp)]
    if sort == "Lowest price":
        evs.sort(key=lambda e: e.total_monthly_huf)
    elif sort == "Shortest commute":
        evs.sort(key=lambda e: e.commute_min if e.commute_min is not None else 999)
    elif sort == "Newest":
        created = {r["id"]: r["created_at"] for r in db.query("SELECT id, created_at FROM listings")}
        evs.sort(key=lambda e: -created.get(e.listing.id, 0))
    st.markdown(f"**{len(evs)}** place(s)")
    if not evs:
        empty("Nothing here with these filters.")
    per = 8
    pages_n = max(1, (len(evs) + per - 1) // per)
    pg = st.session_state.get("match_page", 1)
    pg = min(pg, pages_n)
    favs = favorite_ids()
    for i, ev in enumerate(evs[(pg - 1) * per: pg * per]):
        card(ev, f"m{i}", favs)
    if pages_n > 1:
        a, b, cc = st.columns([1, 2, 1])
        if a.button("← Previous", disabled=pg <= 1):
            st.session_state["match_page"] = pg - 1
            st.rerun()
        b.markdown(f"<div style='text-align:center'>Page {pg} of {pages_n}</div>", unsafe_allow_html=True)
        if cc.button("Next →", disabled=pg >= pages_n):
            st.session_state["match_page"] = pg + 1
            st.rerun()


def criteria_form(c: Criteria) -> Criteria | None:
    with st.form("profile"):
        st.markdown("##### 👤 About you")
        st.caption("Landlords only see this if you allow invitations – never your email or phone number.")
        a, b, cc = st.columns(3)
        name = a.text_input("Name", c.user_name)
        age = b.number_input("Age", 17, 80, c.age)
        gender = cc.selectbox("Gender", GENDERS, index=GENDERS.index(c.gender) if c.gender in GENDERS else 0,
                              help="Only used for shared rooms/beds, where flatmates may ask for a gender.")
        a, b = st.columns(2)
        languages = a.multiselect("Languages you speak", LANGS, c.languages)
        lifestyle = b.selectbox("Lifestyle", ["quiet", "balanced", "social"], ["quiet", "balanced", "social"].index(c.lifestyle))
        about = st.text_input("One line about you", c.about, placeholder="2nd-year economics student, tidy, non-smoker")
        a, b, cc, dd = st.columns(4)
        smoker = a.checkbox("I smoke", c.smoker)
        pet = b.checkbox("I have a pet", c.has_pet)
        docs = cc.checkbox("Lease documents ready", c.documents_ready,
                           help="A yes/no shown to landlords. We never ask which documents or your nationality.")
        publish = dd.checkbox("Landlords may invite me", c.publish)
        st.markdown("##### 🏠 What you're looking for")
        a, b = st.columns(2)
        wanted = a.multiselect("Type of place", TYPES, c.wanted_types, format_func=lambda t: f"{OFFER_ICON[t]} {OFFER_LABEL[t]}")
        budget = b.number_input("Max per month incl. utilities (Ft)", 20_000, 600_000, c.max_total_huf, step=5_000)
        a, b, cc = st.columns(3)
        keys = list(DEST.keys())
        campus = a.selectbox("Where do you study / work?", keys, keys.index(c.campus) if c.campus in keys else 0,
                             format_func=lambda k: DEST[k]["label"])
        campus_url = destination_maps_url(campus)
        if campus_url:
            st.link_button("Open destination in Google Maps", campus_url)
        mode = b.selectbox("Travel mode", ["transit", "walk", "bike"], ["transit", "walk", "bike"].index(c.commute_mode),
                           format_func=lambda m: MODE_LABEL[m])
        commute = cc.slider("Max minutes", 5, 60, c.max_commute_min,
                            help="Places closer score higher (Near / Average / Far). Only beyond "
                                 f"{scoring_config()['commute']['hard_limit_factor']:.0%} of this are they excluded.")
        a, b, cc = st.columns(3)
        rooms = a.select_slider("Min rooms (whole apartments)", [1.0, 1.5, 2.0, 2.5, 3.0], float(c.min_rooms))
        move_in = b.date_input("Move-in date", c.move_in)
        furnished = cc.checkbox("Must be furnished", c.need_furnished)
        amen = st.multiselect("Nice to have", AMENITIES, c.wanted_amenities)
        pref_g = st.multiselect("Flatmates' gender (shared rooms/beds only – empty = anyone)", GENDERS, c.pref_genders)
        if st.form_submit_button("💾 Save profile", type="primary"):
            return c.model_copy(update=dict(
                user_name=name.strip() or c.user_name, age=int(age), gender=gender, languages=languages,
                lifestyle=lifestyle, about=about, smoker=smoker, has_pet=pet, documents_ready=docs, publish=publish,
                wanted_types=wanted or TYPES, max_total_huf=int(budget), campus=campus, commute_mode=mode,
                max_commute_min=commute, min_rooms=rooms, move_in=move_in, need_furnished=furnished,
                wanted_amenities=amen, pref_genders=pref_g))
    return None


def t_search() -> None:
    page_header("🎯 Search & alerts", "Your profile drives every match. Saved searches keep looking and email you.")
    c = my_profile()
    tab_p, tab_s = st.tabs(["👤 Profile & preferences", "🔔 Saved searches & emails"])
    with tab_p:
        new = criteria_form(c)
        if new:
            searches.save_profile(db, user.id, new)
            st.success("Saved – your matches are updated.")
            c = new
        if c.weights_override:
            st.caption("Learned from your 👎 feedback: " + ", ".join(f"{k} ×{v}" for k, v in c.weights_override.items()))
            if st.button("Reset learned preferences"):
                searches.save_profile(db, user.id, c.model_copy(update={"weights_override": {}}))
                st.rerun()
    with tab_s:
        if user.email_opt_out:
            st.warning("You unsubscribed from emails.")
            if st.button("Turn emails back on"):
                db.execute("UPDATE users SET email_opt_out=0 WHERE id=?", (user.id,))
                st.rerun()
        with st.container(border=True):
            st.markdown("**➕ Save my current profile as a search**")
            with st.form("new_search"):
                a, b, cc = st.columns(3)
                name = a.text_input("Name", "My search")
                freq = b.selectbox("Email me", ["instant", "daily", "weekly", "off"],
                                   format_func=lambda f: {"instant": "⚡ Instantly for great matches", "daily": "📅 Daily summary",
                                                          "weekly": "🗓 Weekly summary", "off": "🔕 No emails"}[f])
                th = cc.slider("“Great match” from", 60, 100, 85, help="Instant emails only above this score")
                if st.form_submit_button("Save search", type="primary"):
                    sid, err = searches.create_search_checked(db, user.id, name, c, freq, th,
                                                              min_score=scoring_config()["thresholds"]["maybe"])
                    if err:
                        st.error(err)
                    else:
                        n = searches.run_initial(db, sid)
                        from flatwatch import mailer
                        mailer.process_queue(db)
                        st.success(f"Saved – {n} current match(es) emailed to {user.email}. New ones follow automatically.")
            st.caption(f"Up to {antispam.LIMITS['saved_searches_max']} searches · max "
                       f"{antispam.LIMITS['instant_emails_per_day']} instant emails a day (the rest go to your digest).")
        for s in searches.user_searches(db, user.id):
            sc = Criteria.model_validate_json(s["criteria"])
            n = db.one("SELECT COUNT(*) n FROM notified WHERE search_id=? AND channel<>'none'", (s["id"],))["n"]
            with st.container(border=True):
                a, b = st.columns([3, 2])
                a.markdown(f"**{esc(s['name'])}** {'🟢 active' if s['active'] else '⏸ paused'} · emails: **{s['frequency']}**  \n"
                           f"{huf(sc.max_total_huf)} · {sc.max_commute_min} min to {short(DEST.get(sc.campus, {}).get('label', sc.campus))} · "
                           f"{', '.join(OFFER_LABEL[t].lower() for t in sc.wanted_types)}  \n"
                           f"<span class='fw-small'>{n} match(es) sent · last check {ago(s['last_run_at'])}</span>",
                           unsafe_allow_html=True)
                with b:
                    x1, x2, x3 = st.columns(3)
                    if x1.button("⏸" if s["active"] else "▶️", key=f"p{s['id']}", help="Pause / resume", use_container_width=True):
                        searches.update_search(db, user.id, s["id"], active=0 if s["active"] else 1)
                        st.rerun()
                    if x2.button("↻", key=f"u{s['id']}", help="Update with my current profile", use_container_width=True):
                        searches.update_search(db, user.id, s["id"], criteria=c)
                        st.rerun()
                    if x3.button("🗑", key=f"d{s['id']}", help="Delete", use_container_width=True):
                        searches.delete_search(db, user.id, s["id"])
                        st.rerun()
                    f = st.selectbox("Frequency", ["instant", "daily", "weekly", "off"],
                                     ["instant", "daily", "weekly", "off"].index(s["frequency"]), key=f"f{s['id']}",
                                     label_visibility="collapsed")
                    if f != s["frequency"]:
                        searches.update_search(db, user.id, s["id"], frequency=f)
                        st.rerun()
        if st.button("🔄 Check for new places now"):
            if not antispam.can_check_now(db, user.id):
                st.warning("You've checked a lot this hour – the background search keeps running anyway.")
            else:
                rep = run_cycle(db)
                st.success(f"Done: {rep['match']['instant']} instant alert(s), {rep['match']['queued_for_digest']} for your digest.")


def t_map() -> None:
    page_header("🗺 Map", "Your matches around Debrecen.")
    evs = [e for e in my_evaluations() if e.decision in ("alert", "maybe") and e.lat]
    if not evs:
        empty("No matches with a known location yet.")
        return
    import pandas as pd
    df = pd.DataFrame([{"lat": e.lat, "lon": e.lon, "score": e.match_score, "title": e.listing.title,
                        "color": "#16a34a" if e.decision == "alert" else "#f59e0b", "size": 30 + e.match_score} for e in evs])
    camp = DEST.get(my_profile().campus)
    if camp:
        df = pd.concat([df, pd.DataFrame([{"lat": camp["lat"], "lon": camp["lon"], "score": 0, "title": "Your campus",
                                           "color": "#2563eb", "size": 140}])])
    st.map(df, latitude="lat", longitude="lon", color="color", size="size", height=520,
           alt="Map of matching rental locations and the selected campus")
    st.caption("🟢 great match · 🟠 maybe · 🔵 your campus. Listings without a street address sit at the neighbourhood centre.")


def t_shortlist() -> None:
    page_header("⭐ Shortlist & compare", "Places you saved – side by side.")
    favs = favorite_ids()
    evs = [e for e in my_evaluations() if e.listing.id in favs]
    if not evs:
        empty("Save places with ☆ Save to compare them here.")
        return
    pick = st.multiselect("Compare (max 3)", [e.listing.id for e in evs], [e.listing.id for e in evs][:3], max_selections=3,
                          format_func=lambda i: next(e.listing.title for e in evs if e.listing.id == i))
    chosen = [e for e in evs if e.listing.id in pick]
    if chosen:
        cols = st.columns(len(chosen))
        for col, e in zip(cols, chosen):
            with col:
                with st.container(border=True):
                    html(CARD_MARKER)
                    thumb = photos.first_thumb(db, e.listing.id)
                    img(photos.abs_path(thumb)) if thumb else html(NO_IMAGE)
                    html(f'<div class="fw-row"><div class="fw-title">{esc(e.listing.title)}</div>{ring(e.match_score, e.decision)}</div>'
                         f'<div class="fw-price">{huf(e.total_monthly_huf)} <small>/ month</small></div>'
                         + bars(e.criteria_scores))
    st.subheader("All saved")
    for i, ev in enumerate(evs):
        card(ev, f"s{i}", favs, compact=True)


@st.fragment(run_every=3)
def chat_view(conv_id: int) -> None:
    try:
        msgs = messaging.messages(db, user, conv_id)
    except auth.PermissionDenied:
        st.error("Not your conversation.")
        return
    box = st.container(height=430)
    with box:
        if not msgs:
            empty("No messages yet – say hello 👋")
        for m in msgs[-80:]:
            mine = m["sender_id"] == user.id
            with st.chat_message("user" if mine else "assistant", avatar="🙂" if mine else "🏠"):
                st.markdown(md_escape(m["body"]).replace("\n", "  \n"))   # escaped: no markdown/HTML injection
                flags = m["flags"].split(",") if m["flags"] else []
                if "payment_request" in flags:
                    st.warning("⚠️ Mentions paying in advance. Never pay before a viewing and a signed contract.")
                if "risky_link" in flags:
                    st.error("⚠️ Shortened or unknown link – not clickable here. Be careful.")
                elif "external_link" in flags:
                    st.caption("🔗 Link (as text): " + ", ".join(l["host"] for l in link_report(m["body"])))
                st.caption(f"{datetime.fromtimestamp(m['created_at']):%d %b %H:%M}"
                           + (f" · {'✓✓ read' if m['read_at'] else '✓ sent'}" if mine else ""))


def start_chat_panel() -> None:
    with st.expander("➕ Start a new chat"):
        if user.role == "tenant":
            owned = [e for e in my_evaluations() if e.decision in ("alert", "maybe")
                     and (listings.row(db, e.listing.id) or {"owner_id": None})["owner_id"]][:8]
            st.markdown("**Landlords of your matches**")
            for i, e in enumerate(owned):
                a, b = st.columns([3, 1])
                a.markdown(f"{OFFER_ICON[e.details.offer_type or 'apartment']} {esc(e.listing.title)} · "
                           f"{esc(e.listing.poster_name)} · {e.match_score}%")
                if b.button("💬 Chat", key=f"sc_l{i}", use_container_width=True):
                    cid, err = messaging.start_conversation(db, user, e.listing.id)
                    st.error(err) if err else open_chat(cid)
            me = profile_from_criteria(my_profile(), user.id)
            mates = fm.matches_for(me, [p for p in searches.published_profiles(db) if p.user_id != user.id])[:6]
            st.markdown("**Flatmate matches**")
            for i, m in enumerate(mates):
                a, b = st.columns([3, 1])
                a.markdown(f"👥 {esc(m.b.name)} · {m.score}% · {esc(', '.join(m.reasons[:2]))}")
                if b.button("💬 Chat", key=f"sc_f{i}", use_container_width=True):
                    cid, err = messaging.start_flatmate_chat(db, user, m.b.user_id)
                    st.error(err) if err else open_chat(cid)
        else:
            st.caption("Invite compatible tenants from 🙋 Compatible tenants – tenants who contact you appear here.")


def p_messages() -> None:
    page_header("💬 Messages", "Private chat – your email and phone stay hidden until you choose to share them.")
    start_chat_panel()
    convs = messaging.conversations(db, user)
    if not convs:
        empty("No conversations yet.")
        return
    ids = [c["id"] for c in convs]
    cur = st.session_state.get("open_conv")
    if cur not in ids:
        cur = ids[0]
    left, right = st.columns([1, 2.2], gap="medium")
    with left:
        for c in convs:
            label = (f"{'🔴 ' if c['unread'] else ''}{'👥' if c['kind'] == 'flatmate' else '🏠'} {c['other_name']}"
                     + (f" ({c['unread']})" if c["unread"] else ""))
            if st.button(label, key=f"conv_{c['id']}", use_container_width=True,
                         type="primary" if c["id"] == cur else "secondary"):
                st.session_state["open_conv"] = c["id"]
                st.rerun()
            st.caption(f"{c['listing_title'][:40]} · {ago(c['last_message_at'])}")
    conv = next(c for c in convs if c["id"] == cur)
    with right:
        with st.container(border=True):
            html(CARD_MARKER)
            a, b = st.columns([3, 1])
            a.markdown(f"**{esc(conv['other_name'])}**  \n<span class='fw-small'>{esc(conv['listing_title'])}</span>",
                       unsafe_allow_html=True)
            if conv["listing_id"] and b.button("🔍 Listing", use_container_width=True):
                details(conv["listing_id"])
            chat_view(cur)
            if conv["blocked"]:
                st.info("This conversation is blocked.")
                if st.button("Unblock"):
                    messaging.unblock(db, user, conv["other_id"])
                    st.rerun()
            else:
                with st.form(f"send_{cur}", clear_on_submit=True):
                    x, y = st.columns([5, 1])
                    body = x.text_area("Message", max_chars=2000, label_visibility="collapsed", height=80,
                                       placeholder="Write a message…")
                    if y.form_submit_button("Send ➤", type="primary", use_container_width=True):
                        ok, err = messaging.send(db, user, cur, body)
                        st.error(err) if not ok else st.rerun()
                a, b, _ = st.columns([1, 1, 3])
                with a.popover("🚩 Report"):
                    reason = st.text_area("What happened?", key=f"rr{cur}")
                    if st.button("Send report", key=f"rs{cur}"):
                        messaging.report(db, user, reason or "abusive / scam", target_user_id=conv["other_id"],
                                         conversation_id=cur, listing_id=conv["listing_id"])
                        st.success("Reported – thank you.")
                if b.button("⛔ Block"):
                    messaging.block(db, user, conv["other_id"])
                    st.rerun()


def t_flatmates() -> None:
    page_header("👥 Flatmates", "People whose preferences fit yours and theirs – rent a bigger place together.")
    me = profile_from_criteria(my_profile(), user.id)
    found = fm.matches_for(me, [p for p in searches.published_profiles(db) if p.user_id != user.id])
    if not found:
        empty("No mutual matches yet – make sure “Landlords may invite me” is on in your profile.")
    evs = my_evaluations()
    cols = st.columns(2)
    for i, m in enumerate(found):
        b = m.b
        with cols[i % 2]:
            with st.container(border=True):
                html(CARD_MARKER)
                html(f'<div class="fw-row"><div><div class="fw-title">👤 {esc(b.name)}, {b.age}</div>'
                     f'<div class="fw-sub">{esc(b.gender)} · {esc(short(DEST.get(b.campus, {}).get("label", b.campus)))} · '
                     f'speaks {esc(", ".join(b.languages))}</div></div>{ring(m.score)}</div>'
                     f'<div>{chip("💰 " + huf(b.budget_huf))}{chip("🏡 " + b.lifestyle)}'
                     + (chip("🧪 sample") if b.sample else "") + f'</div><div class="fw-sub">{esc(b.about)}</div>'
                     + ul(m.reasons[:4]))
                flats = fm.flats_for_pair(m, evs)
                if flats:
                    st.caption("🏢 Could share: " + "; ".join(f"{f[0].listing.title} ({huf(f[1])} each)" for f in flats[:2]))
                if st.button("💬 Chat", key=f"fm_{i}", type="primary", use_container_width=True):
                    cid, err = messaging.start_flatmate_chat(db, user, b.user_id)
                    if err:
                        st.error(err)
                    else:
                        if not db.one("SELECT 1 FROM messages WHERE conversation_id=?", (cid,)):
                            messaging.send(db, user, cid, fm.intro_message(m))
                        open_chat(cid)


def t_outside() -> None:
    page_header("📋 Check an outside listing", "Paste an ad from ingatlan.com, Jófogás or Facebook – we read the "
                "Hungarian text, check the real cost, commute and scam signs.")
    with st.form("outside", clear_on_submit=True):
        url = st.text_input("Link *")
        a, b = st.columns(2)
        source = a.selectbox("Site", ["ingatlan.com", "Jófogás", "Facebook csoport", "alberlet.hu", "Other"])
        title = b.text_input("Title *")
        a, b, cc, dd = st.columns(4)
        offer = a.selectbox("Type", TYPES, format_func=lambda t: OFFER_LABEL[t])
        price = b.number_input("Rent (Ft/month) *", 0, 2_000_000, 0, step=5_000)
        size = cc.number_input("m²", 0, 400, 0)
        rooms = dd.number_input("Rooms", 0.0, 10.0, 0.0, step=0.5)
        location = st.text_input("Location / address *")
        desc = st.text_area("Description (Hungarian is fine) *", height=140)
        if st.form_submit_button("Check it", type="primary"):
            if not (url and title and price and location and desc):
                st.error("Fill in the fields marked *")
            elif not antispam.can_paste_outside(db, user.id):
                st.error("Daily limit for checking outside listings reached.")
            else:
                l = RawListing(id=f"X{int(time.time())}", source=source, title=title, price_huf=int(price), location=location,
                               size_m2=int(size) or None, rooms=rooms or None, description=desc, url=url, offer_type=offer)
                listings.upsert(db, l)
                st.cache_data.clear()
                details(l.id)


def p_account() -> None:
    page_header("⚙️ Account & privacy", f"{user.name} · {user.email} · {user.role}")
    a, b = st.columns(2)
    with a:
        with st.container(border=True):
            st.markdown("#### 🔐 Two-factor authentication")
            if user.totp_enabled:
                st.success("2FA is on.")
                with st.form("2fa_off"):
                    pw = st.text_input("Password to turn 2FA off", type="password")
                    if st.form_submit_button("Turn off 2FA"):
                        st.success("2FA turned off.") if auth.disable_totp(db, user, pw) else st.error("Wrong password.")
            else:
                st.caption("Works with Google Authenticator, Microsoft Authenticator, Authy, 1Password…")
                if st.button("Set up 2FA"):
                    st.session_state["totp_setup"] = auth.start_totp(db, user)
                if "totp_setup" in st.session_state:
                    secret, uri = st.session_state["totp_setup"]
                    st.markdown("Add this setup key in your app:")
                    st.code(secret, language=None)
                    with st.form("2fa_on"):
                        code = st.text_input("Then enter the 6-digit code", max_chars=6)
                        if st.form_submit_button("Turn on 2FA", type="primary"):
                            if auth.confirm_totp(db, user, code):
                                st.session_state.pop("totp_setup")
                                st.rerun()
                            st.error("Wrong code – check your phone's clock and try again.")
        with st.container(border=True):
            st.markdown("#### 🛡 Sessions")
            if st.button("Log out on all devices"):
                auth.logout_everywhere(db, user.id)
                st.session_state.pop("token", None)
                st.rerun()
    with b:
        with st.container(border=True):
            st.markdown("#### ✉️ Emails")
            opt = st.toggle("Receive match and message emails", not user.email_opt_out)
            if opt == user.email_opt_out:
                db.execute("UPDATE users SET email_opt_out=? WHERE id=?", (0 if opt else 1, user.id))
                st.rerun()
        with st.container(border=True):
            st.markdown("#### 📦 Your data (GDPR)")
            st.download_button("⬇️ Download all my data (JSON)", auth.export_user_data(db, user.id),
                               file_name="flatwatch-my-data.json", mime="application/json")
            with st.expander("🗑 Delete my account"):
                st.warning("Permanently deletes your account, searches, listings, photos and conversations.")
                with st.form("delete"):
                    pw = st.text_input("Password", type="password")
                    if st.form_submit_button("Delete permanently"):
                        if auth.delete_account(db, user, pw):
                            st.session_state.pop("token", None)
                            st.rerun()
                        st.error("Wrong password.")


def p_inbox() -> None:
    page_header("📬 Test inbox", "See exactly what the platform emails – match alerts, digests, codes, reminders.")
    if user.role == "tenant":
        with st.container(border=True):
            html(CARD_MARKER)
            st.markdown("**🧪 Try the alert flow:** we publish a new listing that fits your profile, the matching engine "
                        "runs, and the alert email appears below within seconds.")
            if not [s for s in searches.user_searches(db, user.id) if s["active"]]:
                st.warning("You need an active saved search first (🎯 Search & alerts → Saved searches).")
            elif st.button("✨ Simulate a new matching listing", type="primary"):
                l = devtools.simulate_listing_for(db, my_profile())
                rep = devtools.run_alerts_now(db)
                st.cache_data.clear()
                st.success(f"Published “{l.title}” – {rep['instant']} instant alert(s), "
                           f"{rep['queued_for_digest']} queued for the digest.")
    inbox_view(user.email if user.role != "admin" else "")


# ================================================================= LANDLORD ==
def listing_form(key: str, existing: RawListing | None = None) -> RawListing | None:
    e = existing
    with st.form(key):
        a, b = st.columns(2)
        prole = a.radio("You are", ["owner", "tenant"], ["owner", "tenant"].index(e.poster_role)
                        if e and e.poster_role in ("owner", "tenant") else 0, horizontal=True, format_func=lambda r: ROLE_LABEL[r])
        offer = b.radio("Offering", TYPES, TYPES.index(e.offer_type) if e and e.offer_type else 0, horizontal=True,
                        format_func=lambda t: f"{OFFER_ICON[t]} {OFFER_LABEL[t]}")
        title = st.text_input("Title *", e.title if e else "", placeholder="Bright room near Kálvin tér, 5 min to tram 1")
        location = st.text_input("Street / area *", e.location if e else "", placeholder="Debrecen, Libakert, Thomas Mann utca",
                                 help="Used for commute times. Don't add the house number.")
        a, b, cc, dd = st.columns(4)
        price = a.number_input("Rent (Ft/month) *", 0, 2_000_000, e.price_huf if e else 0, step=5_000,
                               help="For a bed space: the price of ONE bed")
        incl = b.checkbox("Utilities included", bool(e and e.utilities_included))
        util = cc.number_input("Utilities (Ft/month)", 0, 200_000, (e.utilities_huf or 0) if e else 0, step=1_000)
        dep = dd.number_input("Deposit (Ft)", 0, 3_000_000, (e.deposit_huf or 0) if e else 0, step=10_000)
        a, b, cc, dd = st.columns(4)
        size = a.number_input("Size (m²)", 0, 400, (e.size_m2 or 0) if e else 0)
        rooms = b.number_input("Rooms in the flat", 0.0, 10.0, float(e.rooms or 1) if e else 1.0, step=0.5)
        beds = cc.number_input("Beds in the room", 0, 8, (e.beds_in_room or 0) if e else 0)
        avail = dd.date_input("Available from", e.available_from if e and e.available_from else date.today())
        a, b, cc = st.columns(3)
        furnished = a.checkbox("Furnished", True if not e else bool(e.furnished))
        smoking = b.checkbox("Smoking OK", bool(e and e.smoking_ok))
        pets = cc.checkbox("Pets OK", bool(e and e.pets_ok))
        amen = st.multiselect("Amenities", AMENITIES, e.amenities if e else [])
        st.markdown("**Shared rooms / beds only** – gender is accepted only for shared living; nationality is never asked.")
        a, b = st.columns(2)
        occ = a.multiselect("Current flatmates' genders", GENDERS * 3,
                            [o.get("gender") for o in e.occupants if o.get("gender")] if e else [])
        pref_g = b.multiselect("New flatmate's gender", GENDERS, e.pref_genders if e else [])
        pref_l = st.multiselect("Flatmates' common language (optional)", LANGS, e.pref_languages if e else [])
        desc = st.text_area("Description", e.description if e else "", height=110,
                            help="No phone numbers or links – tenants contact you through the chat.")
        gen = st.checkbox("✨ Write the description for me", value=not bool(e))
        if st.form_submit_button("💾 Save listing", type="primary"):
            if not (title and location and price and (desc or gen)):
                st.error("Fill in title, address, rent and a description.")
                return None
            l = RawListing(
                id=e.id if e else f"F{int(time.time())}", source="Flat-Watch", title=title.strip(), price_huf=int(price),
                location=location.strip(), size_m2=int(size) or None, rooms=rooms or None, description=desc.strip(),
                offer_type=offer, poster_role=prole, poster_name=user.name,
                pref_genders=pref_g if offer != "apartment" else [], pref_languages=pref_l,
                occupants=[{"gender": g} for g in occ] if offer != "apartment" else [], beds_in_room=int(beds) or None,
                utilities_included=incl, utilities_huf=None if incl else (int(util) or None), deposit_huf=int(dep) or None,
                furnished=furnished, available_from=avail, smoking_ok=smoking, pets_ok=pets, amenities=amen)
            if gen and not desc.strip():
                commutes, _, _ = commute_table(l.location, match_district(l.location))
                m = commutes.get("main")
                line = f"About {m['transit']} min to the main university building by public transport" if m else ""
                l.description = describe(l, match_district(l.location) or l.location, line)
            return l
    return None


def photo_manager(listing_id: str) -> None:
    a, b = st.columns([3, 1])
    files = a.file_uploader("Add photos (JPEG/PNG/WEBP, max 10 MB)", type=["jpg", "jpeg", "png", "webp"],
                            accept_multiple_files=True, key=f"up_{listing_id}")
    cat = b.selectbox("Category", PHOTO_CATEGORIES, key=f"cat_{listing_id}")
    if files and st.button("⬆️ Upload", key=f"upb_{listing_id}", type="primary"):
        ok = 0
        for f in files:
            try:
                photos.process_upload(db, listing_id, f.getvalue(), cat)
                ok += 1
            except photos.PhotoError as exc:
                st.error(f"{f.name}: {exc}")
        st.success(f"{ok} photo(s) added – resized, compressed, location data removed.")
        st.cache_data.clear()
    rows = photos.list_photos(db, listing_id)
    if not rows:
        empty("No photos yet – listings with photos get far more enquiries.")
    cols = st.columns(4)
    for i, p in enumerate(rows):
        with cols[i % 4]:
            img(photos.abs_path(p["thumb_path"]))
            newcat = st.selectbox("Category", PHOTO_CATEGORIES, PHOTO_CATEGORIES.index(p["category"])
                                  if p["category"] in PHOTO_CATEGORIES else 6, key=f"pc_{p['id']}", label_visibility="collapsed")
            if newcat != p["category"]:
                photos.set_category(db, p["id"], listing_id, newcat)
                st.rerun()
            x, y, z = st.columns(3)
            if x.button("◀", key=f"pl_{p['id']}", use_container_width=True):
                photos.move_photo(db, p["id"], listing_id, -1)
                st.rerun()
            if y.button("▶", key=f"pr_{p['id']}", use_container_width=True):
                photos.move_photo(db, p["id"], listing_id, 1)
                st.rerun()
            if z.button("🗑", key=f"pd_{p['id']}", use_container_width=True):
                photos.delete_photo(db, p["id"], listing_id)
                st.cache_data.clear()
                st.rerun()


def l_home() -> None:
    deep_link()
    rows = listings.owned_by(db, user.id)
    active = [r for r in rows if r["status"] == "active"]
    due = [r for r in active if time.time() - r["last_verified_at"] > settings.reverify_days * 86400]
    convs = messaging.conversations(db, user)
    html(hero(f"Welcome back, {user.name.split()[0]} 👋", "Your places, your enquiries and the tenants who fit."))
    m = st.columns(4)
    m[0].metric("Active listings", len(active))
    m[1].metric("Conversations", len(convs))
    m[2].metric("Unread messages", messaging.unread_count(db, user))
    m[3].metric("Need reconfirming", len(due))
    review = [r for r in rows if r["status"] == "review"]
    if review:
        st.warning(f"🕵️ {len(review)} listing(s) are waiting for a quick moderation review.")
    st.subheader("Your places")
    if not rows:
        empty("You haven't posted a place yet.")
        if st.button("➕ Post your first place", type="primary"):
            go("post")
        return
    profiles = searches.published_profiles(db)
    cols = st.columns(3)
    for i, r in enumerate(rows[:6]):
        l = RawListing.model_validate_json(r["payload"])
        with cols[i % 3]:
            with st.container(border=True):
                html(CARD_MARKER)
                thumb = photos.first_thumb(db, l.id)
                img(photos.abs_path(thumb)) if thumb else html(NO_IMAGE)
                n_fit = len(tenants_for_offer(l, profiles, listings.median_per_m2(db))[0]) if r["status"] == "active" else 0
                n_conv = sum(1 for c in convs if c["listing_id"] == l.id)
                html(f'<div class="fw-title">{esc(l.title)}</div><div class="fw-price">{huf(l.price_huf)} <small>/ month</small></div>'
                     f'<div>{chip(r["status"], "ok" if r["status"] == "active" else "warn")}{chip(f"🙋 {n_fit} fit")}{chip(f"💬 {n_conv}")}'
                     f'{chip(f"📷 {len(photos.list_photos(db, l.id))}")}</div>')
    if st.button("Manage listings →"):
        go("listings")


def l_listings() -> None:
    page_header("🏘 My listings", "Photos, prices, availability – and what tenants see.")
    rows = listings.owned_by(db, user.id)
    if not rows:
        empty("No listings yet – use ➕ Post a place.")
        return
    for r in rows:
        l = RawListing.model_validate_json(r["payload"])
        due = settings.reverify_days - int((time.time() - r["last_verified_at"]) // 86400)
        conv_n = db.one("SELECT COUNT(*) n FROM conversations WHERE listing_id=?", (l.id,))["n"]
        badge = {"active": "🟢 live", "expired": "🔴 expired", "removed": "⚫ rented", "queued": "⏳ scheduled",
                 "review": "🕵️ in review"}[r["status"]]
        with st.expander(f"{OFFER_ICON[l.offer_type or 'apartment']} **{l.title}** · {huf(l.price_huf)} · {badge} · 💬 {conv_n}",
                         expanded=r["status"] in ("expired", "review")):
            if r["status"] == "review":
                st.warning(f"Held for review: {r['review_reason']}. Edit the listing to fix it, or wait for a moderator.")
            st.caption(f"Availability confirmed {ago(r['last_verified_at'])}"
                       + (f" · reminder in {due} day(s)" if due > 0 else " · please reconfirm"))
            a, b, cc = st.columns(3)
            if a.button("✅ Still available", key=f"rc_{l.id}", use_container_width=True):
                listings.reconfirm(db, l.id)
                st.rerun()
            if b.button("🏁 Rented – hide it", key=f"rm_{l.id}", use_container_width=True):
                listings.set_status(db, l.id, "removed")
                st.rerun()
            cc.caption("Price history: " + " → ".join(huf(h["price_huf"]) for h in listings.price_history(db, l.id)))
            t1, t2 = st.tabs(["📷 Photos", "✏️ Edit"])
            with t1:
                photo_manager(l.id)
            with t2:
                new = listing_form(f"edit_{l.id}", l)
                if new:
                    auth.require_listing_owner(db, user, l.id)
                    status, msg = listings.publish(db, new, user.id)
                    st.cache_data.clear()
                    (st.success if status == "active" else st.warning)(msg)


def l_post() -> None:
    page_header("➕ Post a place", "Owners and tenants subletting a room or bed. Add photos right after saving.")
    l = listing_form("post")
    if l:
        status, msg = listings.publish(db, l, user.id)
        st.cache_data.clear()
        if status is None:
            st.error(msg)
        else:
            (st.success if status == "active" else st.warning)(msg)
            st.info("📷 Now add photos: 🏘 My listings → your listing → Photos.")


def l_tenants() -> None:
    page_header("🙋 Compatible tenants", "Tenants who published their profile and fit your place – scored with their "
                "own preferences. No contact details: invite them, they decide.")
    rows = [r for r in listings.owned_by(db, user.id) if r["status"] == "active"]
    if not rows:
        empty("Post a place first.")
        return
    pick = st.selectbox("Listing", [r["id"] for r in rows],
                        format_func=lambda i: next(RawListing.model_validate_json(r["payload"]).title for r in rows if r["id"] == i))
    l = listings.get(db, pick)
    matches, excluded = tenants_for_offer(l, searches.published_profiles(db), listings.median_per_m2(db))
    st.markdown(f"**{len(matches)}** compatible tenant(s)")
    if excluded:
        st.caption("Not shown: " + ", ".join(f"{n} × {why}" for why, n in excluded.most_common()))
    cols = st.columns(2)
    for i, m in enumerate(matches[:16]):
        p = m.profile
        with cols[i % 2]:
            with st.container(border=True):
                html(CARD_MARKER)
                html(f'<div class="fw-row"><div><div class="fw-title">👤 {esc(p.name)}, {p.age}</div>'
                     f'<div class="fw-sub">{esc(p.gender)} · speaks {esc(", ".join(p.languages))}</div></div>{ring(m.score)}</div>'
                     f'<div>{chip("💰 " + huf(p.budget_huf))}' + (chip("📄 documents ready", "ok") if p.documents_ready else "")
                     + (chip("🧪 sample") if p.sample else "") + f'</div><div class="fw-sub">{esc(p.about)}</div>' + ul(m.reasons))
                with st.popover("✉️ Invite to a viewing", use_container_width=True):
                    body = st.text_area("Message", invite_message(l, m), key=f"inv_{i}", height=140)
                    if st.button("Send invitation", key=f"invb_{i}", type="primary"):
                        cid, err = messaging.landlord_invite(db, user, l.id, p.user_id, body)
                        if err and not cid:
                            st.error(err)
                        else:
                            open_chat(cid)


# ==================================================================== ADMIN ==
def a_moderation() -> None:
    page_header("🛡 Moderation", "Review queue, user reports and bans.")
    m = st.columns(3)
    q = antispam.review_queue(db)
    reps = antispam.open_reports(db)
    m[0].metric("Listings in review", len(q))
    m[1].metric("Open reports", len(reps))
    m[2].metric("Active listings", listings.stats(db).get("active", 0))
    st.subheader("🕵️ Review queue")
    if not q:
        empty("Nothing waiting for review.")
    for r in q:
        l = RawListing.model_validate_json(r["payload"])
        with st.container(border=True):
            st.markdown(f"**{esc(l.title)}** · {huf(l.price_huf)} · by {esc(l.poster_name)}  \n⚠️ {esc(r['review_reason'])}")
            st.caption(l.description[:300])
            a, b, c = st.columns(3)
            if a.button("✅ Approve", key=f"ap_{r['id']}", use_container_width=True):
                antispam.approve_listing(db, r["id"])
                st.rerun()
            if b.button("🗑 Remove", key=f"rmv_{r['id']}", use_container_width=True):
                antispam.remove_listing(db, r["id"])
                st.rerun()
            if r["owner_id"] and c.button("⛔ Ban owner", key=f"ban_{r['id']}", use_container_width=True):
                antispam.ban_user(db, r["owner_id"])
                st.rerun()
    st.subheader("🚩 Reports")
    if not reps:
        empty("No open reports.")
    for r in reps:
        with st.container(border=True):
            st.markdown(f"**{esc(r['reporter'] or 'deleted user')}** reported **{esc(r['target'] or '—')}** "
                        f"{'(listing ' + esc(r['listing_id']) + ')' if r['listing_id'] else ''} · {ago(r['created_at'])}  \n{esc(r['reason'])}")
            a, b = st.columns(2)
            if a.button("Close", key=f"cl_{r['id']}", use_container_width=True):
                antispam.close_report(db, r["id"])
                st.rerun()
            if r["target_user_id"] and b.button("⛔ Ban user", key=f"bu_{r['id']}", use_container_width=True):
                antispam.ban_user(db, r["target_user_id"])
                antispam.close_report(db, r["id"])
                st.rerun()


# =================================================================== layout ==
unread = messaging.unread_count(db, user)
msg_title = f"Messages ({unread})" if unread else "Messages"
P = st.Page
if user.role == "tenant":
    PAGES = {"home": P(t_home, title="Home", icon="🏠", default=True), "matches": P(t_matches, title="Matches", icon="🔎", url_path="matches"),
             "map": P(t_map, title="Map", icon="🗺", url_path="map"), "shortlist": P(t_shortlist, title="Shortlist", icon="⭐", url_path="shortlist"),
             "messages": P(p_messages, title=msg_title, icon="💬", url_path="messages"), "flatmates": P(t_flatmates, title="Flatmates", icon="👥", url_path="flatmates"),
             "search": P(t_search, title="Search & alerts", icon="🎯", url_path="search"), "outside": P(t_outside, title="Check outside ad", icon="📋", url_path="outside"),
             "account": P(p_account, title="Account", icon="⚙️", url_path="account"), "inbox": P(p_inbox, title="Test inbox", icon="📬", url_path="inbox")}
    sections = {"Find": ["home", "matches", "map", "shortlist"], "Connect": ["messages", "flatmates"],
                "Settings": ["search", "outside", "account"] + (["inbox"] if DEV_INBOX else [])}
elif user.role == "landlord":
    PAGES = {"home": P(l_home, title="Dashboard", icon="🏠", default=True), "listings": P(l_listings, title="My listings", icon="🏘", url_path="listings"),
             "post": P(l_post, title="Post a place", icon="➕", url_path="post"), "tenants": P(l_tenants, title="Compatible tenants", icon="🙋", url_path="tenants"),
             "messages": P(p_messages, title=msg_title, icon="💬", url_path="messages"), "account": P(p_account, title="Account", icon="⚙️", url_path="account"),
             "inbox": P(p_inbox, title="Test inbox", icon="📬", url_path="inbox")}
    sections = {"Your places": ["home", "listings", "post", "tenants"], "Connect": ["messages"],
                "Settings": ["account"] + (["inbox"] if DEV_INBOX else [])}
else:
    PAGES = {"mod": P(a_moderation, title="Moderation", icon="🛡", default=True), "inbox": P(p_inbox, title="Test inbox", icon="📬", url_path="inbox"),
             "account": P(p_account, title="Account", icon="⚙️", url_path="account")}
    sections = {"Admin": ["mod", "inbox", "account"]}

nav = st.navigation({k: [PAGES[x] for x in v] for k, v in sections.items()})

with st.sidebar:
    html(f'<div class="fw-stat"><b>{"🔎" if user.role == "tenant" else "🏠" if user.role == "landlord" else "🛡"} '
         f'{esc(user.name)}</b><br><span class="fw-small">{esc(user.email)}'
         f'{" · 🔐 2FA" if user.totp_enabled else ""}</span></div>')
    if unread:
        st.warning(f"💬 {unread} unread message(s)")
    with st.expander("➕ Add a campus or place"):
        with st.form("add_place", clear_on_submit=True):
            label = st.text_input("Name", placeholder="Faculty of Music")
            address = st.text_input("Address", placeholder="Debrecen, Kodály Zoltán utca 2")
            coords = st.text_input("…or lat, lon", placeholder="47.5536, 21.6215")
            if st.form_submit_button("Add place"):
                try:
                    lat = lon = None
                    if coords.strip():
                        lat, lon = (float(x) for x in coords.replace(" ", "").split(","))
                    ok, msg = add_destination(label, address, lat, lon) if label else (False, "Name required")
                    if ok:
                        st.cache_data.clear()
                        st.rerun()
                    st.error(msg)
                except ValueError:
                    st.error("Coordinates must look like 47.5536, 21.6215")
    last = db.kv_get("worker_last_run")
    st.caption(f"🤖 Background search: {'running' if worker.running else 'separate process'}"
               + (f" · {ago(last['at'])}" if last else "") + f"  \n✉️ Email: {'test mail server' if LOCAL_MAIL else 'SMTP' if settings.smtp_enabled else 'outbox files'}"
               f"  \n🚋 Transit: {'all lines (OSM)' if transit.source() == 'openstreetmap' else 'trams only'}")
    if st.button("Log out", use_container_width=True):
        auth.logout(db, st.session_state.pop("token", None))
        st.rerun()

nav.run()

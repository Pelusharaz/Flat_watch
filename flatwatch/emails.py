"""Email templates (HTML + plain text). All user/listing text is HTML-escaped."""
from __future__ import annotations

import html
from typing import Optional

from .config import settings
from .models import OFFER_LABEL, Evaluation

BRAND = "#c0392b"


def _wrap(title: str, body: str, unsub_token: Optional[str] = None) -> str:
    unsub = (f'<p style="font-size:12px;color:#888">You get this because you have an active search on Flat-Watch. '
             f'<a href="{settings.app_base_url}/?unsubscribe={unsub_token}">Unsubscribe</a> · '
             f'<a href="{settings.app_base_url}/search">Manage notifications</a></p>') if unsub_token else ""
    return f"""<!doctype html><html><body style="font-family:Arial,sans-serif;max-width:640px;margin:auto;color:#222">
<h2 style="color:{BRAND}">🏠 Flat-Watch Debrecen</h2><h3>{html.escape(title)}</h3>{body}
<hr style="border:none;border-top:1px solid #eee">{unsub}
<p style="font-size:12px;color:#888">Never pay a deposit before you have seen a place and signed a contract.</p>
</body></html>"""


def code_email(kind: str, name: str, code: str) -> tuple[str, str, str]:
    what = "verify your email" if kind == "verify" else "reset your password"
    subject = f"Your Flat-Watch code: {code}"
    body = (f"<p>Hi {html.escape(name)},</p><p>Use this code to {what}:</p>"
            f'<p style="font-size:28px;letter-spacing:6px;font-weight:bold">{code}</p>'
            f"<p>It expires in 15 minutes. If you didn't request it, ignore this email.</p>")
    text = f"Hi {name},\n\nUse this code to {what}: {code}\nIt expires in 15 minutes."
    return subject, _wrap("Your one-time code", body), text


def photo_url(rel_path: Optional[str]) -> Optional[str]:
    return f"{settings.app_base_url}/app/static/{rel_path}" if rel_path else None


def _listing_block(ev: Evaluation, thumb: Optional[str]) -> tuple[str, str]:
    l, d = ev.listing, ev.details
    offer = OFFER_LABEL[d.offer_type or "apartment"]
    link = f"{settings.app_base_url}/?listing={l.id}"
    img = (f'<img src="{html.escape(photo_url(thumb))}" width="180" style="border-radius:8px;float:left;margin-right:12px">'
           if thumb else "")
    reasons = "".join(f"<li>{html.escape(r)}</li>" for r in ev.positives[:3])
    minus = "".join(f"<li style='color:#a55'>{html.escape(r)}</li>" for r in ev.negatives[:2])
    price = f"{ev.total_monthly_huf:,}".replace(",", " ")
    block = f"""<div style="border:1px solid #eee;border-radius:10px;padding:12px;margin:12px 0;overflow:hidden">
{img}<b style="font-size:16px">{html.escape(l.title)}</b><br>
<span style="color:{BRAND};font-weight:bold">{ev.match_score}% match</span> · {offer} · {price} Ft/month incl. utilities<br>
📍 {html.escape(d.neighborhood or l.location)} · {l.size_m2 or '?'} m² · {ev.commute_min if ev.commute_min is not None else '?'} min to your campus ({ev.commute_category})
<ul style="margin:6px 0">{reasons}{minus}</ul>
<a href="{link}" style="background:{BRAND};color:#fff;padding:6px 12px;border-radius:6px;text-decoration:none">View & message the landlord</a>
</div>"""
    text = (f"- {l.title} — {ev.match_score}% match, {offer}, {price} Ft/month, "
            f"{d.neighborhood or l.location}, {ev.commute_min} min. {link}")
    return block, text


def matches_email(name: str, kind: str, items: list[tuple[Evaluation, Optional[str]]],
                  search_name: str, unsub_token: str) -> tuple[str, str, str]:
    titles = {"first": f"Your first matches for “{search_name}”",
              "instant": "New place that fits you",
              "daily": f"Your daily matches for “{search_name}”",
              "weekly": f"Your weekly matches for “{search_name}”"}
    title = titles.get(kind, "New matches")
    blocks, texts = zip(*(_listing_block(ev, t) for ev, t in items)) if items else ((), ())
    subject = (f"🏠 {items[0][0].match_score}% match: {items[0][0].listing.title}" if kind == "instant"
               else f"🏠 {len(items)} place(s) for you – {search_name}")
    body = f"<p>Hi {html.escape(name)},</p>" + "".join(blocks)
    text = f"Hi {name},\n\n{title}\n\n" + "\n".join(texts) + f"\n\nUnsubscribe: {settings.app_base_url}/?unsubscribe={unsub_token}"
    return subject, _wrap(title, body, unsub_token), text


def reconfirm_email(name: str, title: str, token: str) -> tuple[str, str, str]:
    link = f"{settings.app_base_url}/?reconfirm={token}"
    body = (f"<p>Hi {html.escape(name)},</p><p>Is <b>{html.escape(title)}</b> still available? "
            f"Listings that aren't confirmed are hidden after {settings.expire_days} days so tenants don't waste time.</p>"
            f'<p><a href="{link}">✅ Yes, still available</a></p>')
    return (f"Is “{title}” still available?", _wrap("Please confirm your listing", body),
            f"Hi {name},\n\nIs '{title}' still available? Confirm: {link}")


def new_message_email(name: str, other: str, listing_title: str) -> tuple[str, str, str]:
    link = f"{settings.app_base_url}/messages"
    body = (f"<p>Hi {html.escape(name)},</p><p><b>{html.escape(other)}</b> sent you a message about "
            f"<b>{html.escape(listing_title)}</b>.</p><p><a href=\"{link}\">Read it on Flat-Watch</a></p>"
            "<p style='font-size:12px;color:#888'>For your privacy, message contents are not sent by email.</p>")
    return (f"New message about {listing_title}", _wrap("You have a new message", body),
            f"{other} sent you a message about {listing_title}: {link}")

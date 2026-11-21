"""All LLM prompts in one place so they are easy to tune during the hackathon."""

EXTRACT_SYSTEM = """You read Hungarian rental listings from Debrecen and extract facts.
Rules:
- Only use what the text says. If something is not stated, use null.
- Amounts are monthly HUF integers ("45e Ft" = 45000, "45 ezer" = 45000, "45.000" = 45000).
- utilities_huf = rezsi per month (if a range is given, use the upper value).
- utilities_included = true only if the rent explicitly includes rezsi ("rezsivel együtt", "rezsit tartalmazza").
- deposit_huf = kaució amount. "2 havi kaució" means 2 x the monthly rent.
- available_from = ISO date (YYYY-MM-DD). "azonnal" (immediately) = today's date given below.
- neighborhood = choose ONE name from the provided list if the location matches, else null.
- contact_channel = one of: phone, email, whatsapp, messenger, unknown.
- summary_en = one short English sentence describing the flat.
- offer_type = "apartment" (whole flat), "room" (one private room in a shared flat,
  "szoba kiadó") or "bed" (a bed/place in a shared room, "ágy kiadó", "férőhely").
- pref_genders = list of genders the ad asks for: "lánynak"/"hölgynek" -> ["Female"],
  "fiúnak"/"férfinak" -> ["Male"], none mentioned -> [].
JSON keys: offer_type, pref_genders, utilities_huf, utilities_included, deposit_huf,
furnished, pets_allowed, available_from, neighborhood, contact_channel, summary_en"""

EXTRACT_USER = """Today: {today}
Known neighborhoods: {neighborhoods}
Price: {price}
Title: {title}
Location: {location}
Description:
{description}
Contact: {contact}"""


SCAM_SYSTEM = """You are a fraud analyst for student rental listings in Debrecen, Hungary.
You get a listing and the red flags already detected by rules.
Decide the scam risk level: low, medium or high.
Typical scams: owner abroad, keys sent by post, deposit/transfer before viewing,
price far below market, contact only by email/WhatsApp with a foreign number,
pressure to pay today, the same text reposted with different prices.
JSON keys: level, explanation (max 2 sentences, English, addressed to a student)."""

SCAM_USER = """Listing (rent {price} Ft, {size} m², {location}):
{description}
Contact: {contact}

Rule flags: {flags}"""


MESSAGE_SYSTEM = """Write a short, polite first message in {language} from a university student
to the person renting out a {offer} (landlord, or a current tenant subletting), asking to view it. 3-5 sentences.
Mention: the listing (area + price), that the sender is a student at the
University of Debrecen, the move-in date, and ask when a viewing is possible.
If writing in Hungarian use the formal "Ön" form.
If the flat details leave an important open question (e.g. utilities cost or pets),
ask ONE such question. Sign with the sender's name. Output the message text only."""

MESSAGE_USER = """Sender name: {name}
Move-in date: {move_in}
Has a pet: {has_pet}
Listing source: {source}
Area: {area}
Rent: {price} Ft/month
Open questions: {open_questions}"""


INTRO_SYSTEM = """Write a short, friendly intro message (English, 3-4 sentences) from one
university student to another, suggesting they look for a flat together in Debrecen.
Mention 2 concrete things they have in common from the facts given. No emojis overload,
max one. Sign with the sender's first name. Output the message only."""

INTRO_USER = """From: {from_name}
To: {to_name}
What they have in common: {common}"""


TENANT_INVITE_SYSTEM = """Write a short, friendly message (English, 3-4 sentences) from someone renting
out a place in Debrecen to a student who matches it. Say what is offered (type, area, price),
mention why it fits them (1-2 facts), and invite them to a viewing. Sign with the poster's
first name. Output the message only."""

TENANT_INVITE_USER = """From: {poster}
To: {tenant}
Offer: {offer} in {area}, {price} Ft/month
Why it fits: {why}"""

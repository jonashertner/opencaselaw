"""
OpenCaseLaw Pro — Stripe billing, license management, and verify proxy.

License keys are stored in a local SQLite DB. Stripe webhooks create/update
licenses. Pro users call /api/billing/verify which proxies to Anthropic.

Environment variables:
  STRIPE_SECRET_KEY          — Stripe API key (sk_live_... or sk_test_...)
  STRIPE_WEBHOOK_SECRET      — Stripe webhook signing secret (whsec_...)
  STRIPE_PRICE_ID            — Stripe Price ID for CHF 5/month subscription
  ANTHROPIC_API_KEY          — Server-side Anthropic key for Pro verify proxy
  SWISS_CASELAW_LICENSES_DB  — Path to licenses.db (default: output/licenses.db)
"""

import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone

import httpx

logger = logging.getLogger("stripe_billing")

# ── Config ───────────────────────────────────────────────────

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

_data_dir = os.environ.get("SWISS_CASELAW_DIR", os.path.expanduser("~/.swiss-caselaw"))
LICENSES_DB = os.environ.get(
    "SWISS_CASELAW_LICENSES_DB",
    os.path.join(_data_dir, "licenses.db"),
)

# Pro verify: max tokens and model
VERIFY_MODEL = "claude-sonnet-4-6"
VERIFY_MAX_TOKENS = 500
PARSE_MODEL = "claude-haiku-4-5-20251001"
VERIFY_SYSTEM_PROMPT_TEMPLATE = (
    "You are a Swiss legal reference verification assistant. "
    "Given a text passage that cites a court decision, and the full text of that decision, "
    "determine whether the decision supports the legal principle claimed in the text.\n\n"
    "IMPORTANT: Read the ENTIRE decision text carefully, including all Erwägungen. "
    "A claim is 'supports' if the decision establishes or confirms the stated principle, "
    "even if the decision uses different terminology or the principle appears as part of "
    "a broader legal reasoning. Legal paraphrasing is normal — focus on substance, not exact wording. "
    "'partial' means the decision addresses the topic but the connection is indirect, "
    "the terminology differs, or you are not fully certain — err on the side of 'partial' rather than 'contradicts'. "
    "'contradicts' means the decision EXPRESSLY establishes the opposite principle — use this ONLY when clearly contradicted. "
    "If uncertain, say 'partial' and explain what to check. "
    "NEVER claim the text is truncated or broken — you have the complete decision text (marked with [Ende des Entscheids]). "
    "If a claimed principle is not found, say it was not found in this decision, not that the text is incomplete.\n\n"
    "The passage may contain placeholders such as [NAME_1], [EMAIL_1] or [AHV_1]. They replace personal data that was removed before transmission. Treat each placeholder as an opaque proper noun: do not guess what it stands for, do not comment on it, and do not reproduce it except when quoting the passage.\n\n"
    "Respond ONLY in valid JSON. Write the explanation and quote in {lang_name}:\n"
    "{{\n"
    '  "verdict": "supports" or "partial" or "contradicts",\n'
    '  "explanation": "Brief explanation in {lang_name} (2-3 sentences max)",\n'
    '  "relevant_erwaegung": "The most relevant E./consid. number, e.g. \'3\' or \'2.1\'",\n'
    '  "quote": "Key quote from the decision (max 200 chars)"\n'
    "}}"
)

LANG_NAMES = {"de": "German", "fr": "French", "it": "Italian", "en": "English"}

# Rate limit: max verifications per license per day
PRO_DAILY_LIMIT = 25


# ── License DB ───────────────────────────────────────────────

def _get_db():
    """Open (and create if needed) the licenses database."""
    db = sqlite3.connect(LICENSES_DB, timeout=5)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=3000")
    db.execute("""
        CREATE TABLE IF NOT EXISTS licenses (
            license_key     TEXT PRIMARY KEY,
            email           TEXT NOT NULL,
            stripe_customer TEXT,
            stripe_sub      TEXT,
            status          TEXT NOT NULL DEFAULT 'active',
            created_at      TEXT NOT NULL,
            cancelled_at    TEXT,
            usage_today     INTEGER DEFAULT 0,
            usage_date      TEXT
        )
    """)
    db.commit()
    return db


def log_pro_usage(key: str, feature: str):
    """Log a Pro feature usage for analytics."""
    db = _get_db()
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS pro_usage_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                license_key TEXT NOT NULL,
                feature TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
        """)
        db.execute(
            "INSERT INTO pro_usage_log (license_key, feature, timestamp) VALUES (?, ?, ?)",
            (key, feature, datetime.now(timezone.utc).isoformat()),
        )
        db.commit()
    except Exception:
        pass
    finally:
        db.close()


def get_pro_usage_stats() -> dict:
    """Get Pro feature usage stats for admin dashboard."""
    db = _get_db()
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS pro_usage_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                license_key TEXT NOT NULL,
                feature TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
        """)
        # Today's usage by feature
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_rows = db.execute(
            "SELECT feature, COUNT(*) FROM pro_usage_log WHERE timestamp LIKE ? GROUP BY feature ORDER BY COUNT(*) DESC",
            (today + "%",),
        ).fetchall()
        # All-time usage by feature
        all_rows = db.execute(
            "SELECT feature, COUNT(*) FROM pro_usage_log GROUP BY feature ORDER BY COUNT(*) DESC",
        ).fetchall()
        # Last 10 calls
        recent = db.execute(
            "SELECT license_key, feature, timestamp FROM pro_usage_log ORDER BY id DESC LIMIT 10",
        ).fetchall()
        return {
            "today": {r[0]: r[1] for r in today_rows},
            "all_time": {r[0]: r[1] for r in all_rows},
            "recent": [{"key": r[0][:20] + "...", "feature": r[1], "time": r[2]} for r in recent],
        }
    except Exception:
        return {"today": {}, "all_time": {}, "recent": []}
    finally:
        db.close()


def create_license(email: str, stripe_customer: str = "", stripe_sub: str = "") -> str:
    """Create a new license key and store it. Returns the key."""
    key = "ocl_pro_" + secrets.token_hex(20)
    now = datetime.now(timezone.utc).isoformat()
    db = _get_db()
    try:
        db.execute(
            "INSERT INTO licenses (license_key, email, stripe_customer, stripe_sub, status, created_at) "
            "VALUES (?, ?, ?, ?, 'active', ?)",
            (key, email, stripe_customer, stripe_sub, now),
        )
        db.commit()
    finally:
        db.close()
    logger.info("License created for %s (sub=%s)", email, stripe_sub)
    return key


def validate_license(key: str) -> dict | None:
    """Check if a license key is valid. Returns license dict or None."""
    if not key or not key.startswith("ocl_pro_"):
        return None
    db = _get_db()
    try:
        row = db.execute(
            "SELECT license_key, email, status, usage_today, usage_date FROM licenses WHERE license_key = ?",
            (key,),
        ).fetchone()
    finally:
        db.close()
    if not row:
        return None
    status = row[2]
    if status != "active":
        return None
    return {
        "license_key": row[0],
        "email": row[1],
        "status": status,
        "usage_today": row[3] or 0,
        "usage_date": row[4] or "",
    }


def increment_usage(key: str) -> bool:
    """Increment daily usage for a license. Returns False if over limit."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    db = _get_db()
    try:
        # Reset if new day
        db.execute(
            "UPDATE licenses SET usage_today = 0, usage_date = ? WHERE license_key = ? AND status = 'active' AND (usage_date != ? OR usage_date IS NULL)",
            (today, key, today),
        )
        # Atomic increment with limit check
        cursor = db.execute(
            "UPDATE licenses SET usage_today = usage_today + 1 WHERE license_key = ? AND status = 'active' AND usage_date = ? AND usage_today < ?",
            (key, today, PRO_DAILY_LIMIT),
        )
        db.commit()
        return cursor.rowcount > 0
    finally:
        db.close()


def cancel_license(stripe_sub: str):
    """Mark a license as cancelled by subscription ID."""
    now = datetime.now(timezone.utc).isoformat()
    db = _get_db()
    try:
        db.execute(
            "UPDATE licenses SET status = 'cancelled', cancelled_at = ? WHERE stripe_sub = ?",
            (now, stripe_sub),
        )
        db.commit()
    finally:
        db.close()
    logger.info("License cancelled for sub=%s", stripe_sub)


def reactivate_license(stripe_sub: str):
    """Reactivate a cancelled license by subscription ID."""
    db = _get_db()
    try:
        db.execute(
            "UPDATE licenses SET status = 'active', cancelled_at = NULL WHERE stripe_sub = ?",
            (stripe_sub,),
        )
        db.commit()
    finally:
        db.close()
    logger.info("License reactivated for sub=%s", stripe_sub)


def get_license_by_session(session_id: str) -> dict | None:
    """Look up license by Stripe checkout session ID. Fetches session from Stripe API to get subscription ID."""
    import re
    if not session_id or not re.match(r'^cs_(test_|live_)?[a-zA-Z0-9]+$', session_id):
        return None
    if not STRIPE_SECRET_KEY:
        return None
    try:
        resp = httpx.get(
            f"https://api.stripe.com/v1/checkout/sessions/{session_id}",
            auth=(STRIPE_SECRET_KEY, ""),
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        session = resp.json()
        sub_id = session.get("subscription", "")
        if not sub_id:
            return None
        return get_license_by_sub(sub_id)
    except Exception:
        return None


def get_license_by_sub(stripe_sub: str) -> dict | None:
    """Look up license by Stripe subscription ID."""
    db = _get_db()
    try:
        row = db.execute(
            "SELECT license_key, email, status FROM licenses WHERE stripe_sub = ?",
            (stripe_sub,),
        ).fetchone()
    finally:
        db.close()
    if not row:
        return None
    return {"license_key": row[0], "email": row[1], "status": row[2]}


# ── Stripe Customer Portal ────────────────────────────────────

def create_portal_session(customer_id: str, return_url: str) -> dict:
    """Create a Stripe Customer Portal session for subscription management."""
    if not STRIPE_SECRET_KEY or not customer_id:
        return {"error": "Not configured"}

    resp = httpx.post(
        "https://api.stripe.com/v1/billing_portal/sessions",
        auth=(STRIPE_SECRET_KEY, ""),
        data={
            "customer": customer_id,
            "return_url": return_url,
        },
        timeout=15,
    )
    if resp.status_code != 200:
        logger.error("Portal session error: %s", resp.text[:200])
        return {"error": "Failed to create portal session"}

    data = resp.json()
    return {"portal_url": data.get("url", "")}


def get_customer_for_license(key: str) -> str | None:
    """Get the Stripe customer ID for a license key."""
    db = _get_db()
    try:
        row = db.execute(
            "SELECT stripe_customer FROM licenses WHERE license_key = ? AND status = 'active'",
            (key,),
        ).fetchone()
    finally:
        db.close()
    return row[0] if row else None


# ── Stripe Checkout ──────────────────────────────────────────

def create_checkout_session(success_url: str, cancel_url: str, locale: str = "") -> dict:
    """Create a Stripe Checkout session for CHF 5/month subscription."""
    if not STRIPE_SECRET_KEY or not STRIPE_PRICE_ID:
        return {"error": "Stripe not configured"}

    request_data = {
        "mode": "subscription",
        "line_items[0][price]": STRIPE_PRICE_ID,
        "line_items[0][quantity]": "1",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "allow_promotion_codes": "true",
    }
    if locale:
        request_data["locale"] = locale

    resp = httpx.post(
        "https://api.stripe.com/v1/checkout/sessions",
        auth=(STRIPE_SECRET_KEY, ""),
        data=request_data,
        timeout=15,
    )
    if resp.status_code != 200:
        logger.error("Stripe checkout error: %s", resp.text)
        return {"error": "Failed to create checkout session"}

    data = resp.json()
    return {"checkout_url": data.get("url", ""), "session_id": data.get("id", "")}


# ── Stripe Webhook ───────────────────────────────────────────

def _verify_stripe_signature(payload: bytes, sig_header: str) -> bool:
    """Verify Stripe webhook signature (v1 scheme)."""
    if not STRIPE_WEBHOOK_SECRET:
        return False
    try:
        parts = dict(pair.split("=", 1) for pair in sig_header.split(","))
        timestamp = parts.get("t", "")
        expected_sig = parts.get("v1", "")
        # Check timestamp is within 5 minutes
        try:
            if abs(time.time() - int(timestamp)) > 300:
                return False
        except (ValueError, TypeError):
            return False
        signed_payload = f"{timestamp}.".encode() + payload
        computed = hmac.new(
            STRIPE_WEBHOOK_SECRET.encode(), signed_payload, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(computed, expected_sig)
    except Exception:
        return False


def handle_webhook(payload: bytes, sig_header: str) -> dict:
    """Process Stripe webhook event. Returns status dict."""
    if not _verify_stripe_signature(payload, sig_header):
        return {"error": "Invalid signature", "status": 400}

    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        return {"error": "Invalid JSON", "status": 400}

    event_type = event.get("type", "")
    obj = event.get("data", {}).get("object", {})

    if event_type == "checkout.session.completed":
        # New subscription — create license
        customer = obj.get("customer", "")
        email = obj.get("customer_details", {}).get("email", "") or obj.get("customer_email", "")
        sub_id = obj.get("subscription", "")
        if email and sub_id:
            # Check if license already exists for this subscription
            existing = get_license_by_sub(sub_id)
            if not existing:
                key = create_license(email, customer, sub_id)
                logger.info("New Pro license: %s (%s)", email, key[:20] + "...")
                # TODO: send email with license key via SendGrid/Postmark
        return {"status": 200, "action": "license_created"}

    elif event_type == "customer.subscription.deleted":
        sub_id = obj.get("id", "")
        if sub_id:
            cancel_license(sub_id)
        return {"status": 200, "action": "license_cancelled"}

    elif event_type == "customer.subscription.updated":
        sub_id = obj.get("id", "")
        status = obj.get("status", "")
        if sub_id:
            if status == "active":
                reactivate_license(sub_id)
            elif status in ("canceled", "unpaid", "past_due"):
                cancel_license(sub_id)
        return {"status": 200, "action": "license_updated"}

    elif event_type == "invoice.payment_failed":
        # Don't cancel immediately — Stripe retries. Cancel only on subscription.deleted
        logger.warning("Payment failed for sub=%s", obj.get("subscription", ""))
        return {"status": 200, "action": "payment_failed_logged"}

    return {"status": 200, "action": "ignored"}


# ── Pro Verify Proxy ─────────────────────────────────────────

def verify_reference_pro(
    selected_text: str,
    case_brief: dict,
    case_ref: str,
    lang: str = "de",
) -> dict:
    """Call Anthropic API to verify a reference. Server-side for Pro users."""
    if not ANTHROPIC_API_KEY:
        return {"error": "Anthropic API not configured on server"}

    # Send the complete decision text — Sonnet handles up to 200K tokens
    raw_text = case_brief.get("full_text") or case_brief.get("regeste") or ""
    brief_text = raw_text + "\n\n[Ende des Entscheids]"

    resp = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        json={
            "model": VERIFY_MODEL,
            "max_tokens": VERIFY_MAX_TOKENS,
            "system": VERIFY_SYSTEM_PROMPT_TEMPLATE.format(lang_name=LANG_NAMES.get(lang, "German")),
            "messages": [{
                "role": "user",
                "content": (
                    f'Text passage to verify:\n'
                    f'"{selected_text}"\n\n'
                    f'Content of the cited decision ({case_ref}):\n{brief_text}'
                ),
            }],
        },
        timeout=30,
    )

    if resp.status_code != 200:
        logger.error("Anthropic API error: %s %s", resp.status_code, resp.text[:200])
        return {"error": "Verification service error"}

    data = resp.json()
    content = (data.get("content") or [{}])[0].get("text", "")

    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        # Try to extract JSON from response
        import re
        m = re.search(r"\{[\s\S]*\}", content)
        if m:
            result = json.loads(m.group())
        else:
            return {"error": "Could not parse verification result"}

    return result


# ── Find Supporting Decisions (Haiku helpers) ─────────────────

PARSE_STATEMENT_PROMPT = (
    "You are a Swiss legal research assistant with expert knowledge of Swiss law.\n\n"
    "Swiss law reference: Mietrecht/Pachtrecht = OR Art. 253-304 (NOT ZGB). "
    "Arbeitsrecht = OR Art. 319-362. Auftragsrecht = OR Art. 394-406. "
    "Haftpflicht = OR Art. 41-61. Sachenrecht = ZGB Art. 641-977. "
    "Personenrecht = ZGB Art. 11-89. Familienrecht = ZGB Art. 90-456. "
    "Erbrecht = ZGB Art. 457-640. Strafrecht = StGB. Verfassungsrecht = BV.\n\n"
    "Given a legal statement, extract the claim and generate search queries "
    "to find Swiss BGE (Leitentscheide) and BGer decisions that establish this principle.\n\n"
    "The statement may contain placeholders such as [NAME_1] or [ADDRESS_1]. They replace personal data "
    "removed before transmission. Treat them as opaque proper nouns, never guess what they stand for, "
    "and leave them out of the queries — search for the legal principle, not the parties.\n\n"
    "Respond ONLY in valid JSON:\n"
    "{\n"
    '  "claim": "The core legal claim in 1-2 sentences",\n'
    '  "legal_area": "e.g. Mietrecht, Arbeitsrecht",\n'
    '  "queries": ["query1", "query2", "query3"],\n'
    '  "statutes": ["Art. 271 OR", "Art. 271a OR"]\n'
    "}\n\n"
    "IMPORTANT:\n"
    "- Generate queries in the SAME LANGUAGE as the statement\n"
    "- First query: the most distinctive legal phrase in quotes for exact match (e.g. '\"begründeter Anlass\" fristlose Entlassung')\n"
    "- Second query: include the statute article + key terms (e.g. 'Art. 271 OR Kündigung Treu und Glauben')\n"
    "- Third query: the core legal concept WITHOUT domain-specific context, to find the principle across different legal areas (e.g. if the statement is about 'Wiederholungsgefahr bei Vertragsverletzung', search for 'Wiederholungsgefahr Zeitpunkt Urteilsfällung Unterlassungsklage' — the principle may originate from a different legal domain)\n"
    "- Use CORRECT statute references (Mietrecht = OR, not ZGB)\n"
    "- Prefer queries that will find BGE Leitentscheide"
)

SCORE_SUPPORT_PROMPT = (
    "You are a Swiss legal research assistant. Given a legal statement and a court decision summary, "
    "determine how well this decision supports the statement.\n\n"
    "Placeholders such as [NAME_1] in the statement replace personal data removed before transmission. "
    "Treat them as opaque proper nouns and do not mention them in the explanation.\n\n"
    "Respond ONLY in valid JSON:\n"
    "{\n"
    '  "relevance": 0-100 (how relevant this decision is to the statement),\n'
    '  "supports": true/false (does it support the claim?),\n'
    '  "explanation": "1 sentence explaining why this decision is relevant (in the statement\'s language)",\n'
    '  "key_passage": "The most relevant quote from the regeste/text (max 150 chars)"\n'
    "}"
)


def parse_legal_statement(statement: str) -> dict:
    """Use Haiku to extract legal claim and generate search queries."""
    api_key = ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"error": "AI not configured"}

    resp = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        json={
            "model": PARSE_MODEL,
            "max_tokens": 300,
            "system": PARSE_STATEMENT_PROMPT,
            "messages": [{"role": "user", "content": f"Statement:\n\"{statement}\""}],
        },
        timeout=15,
    )

    if resp.status_code != 200:
        logger.error("Parse statement error: %s", resp.text[:200])
        return {"error": "AI service error"}

    content = (resp.json().get("content") or [{}])[0].get("text", "")
    # Strip markdown code fences if present
    import re
    content = re.sub(r"^```(?:json)?\s*", "", content.strip())
    content = re.sub(r"\s*```$", "", content.strip())
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", content)
        if m:
            return json.loads(m.group())
        return {"error": "Could not parse AI response"}


def score_supporting_results(statement: str, results: list, api_key_override: str = "", lang: str = "de") -> list:
    """Use Haiku to score how well each result supports the statement."""
    api_key = api_key_override or ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY", "")
    logger.info("score_supporting_results: key=%s results=%d", bool(api_key), len(results))
    if not api_key or not results:
        return results

    # Build a batch scoring prompt
    import re as _re
    summaries = []
    for i, r in enumerate(results[:12]):
        regeste = r.get("regeste") or _re.sub(r"<[^>]*>", "", r.get("snippet") or "") or r.get("title") or ""
        regeste = regeste[:300]
        if not regeste.strip():
            regeste = "(no summary available)"
        summaries.append(f"[{i}] {r.get('docket_number', '?')}: {regeste}")

    resp = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        json={
            "model": VERIFY_MODEL,
            "max_tokens": 2500,
            "system": (
                "You are a Swiss legal research assistant. Score how well each court decision supports "
                "the given legal statement.\n\n"
                "Scoring criteria:\n"
                "- 90-100: Decision directly establishes or confirms the exact legal principle stated\n"
                "- 70-89: Decision addresses the same legal question with relevant reasoning\n"
                "- 40-69: Decision is in the same legal area but doesn't directly address the claim\n"
                "- 0-39: Decision is not relevant\n\n"
                "Authority bonus: BGE/ATF/DTF (Leitentscheide) are more authoritative than unreported decisions.\n\n"
                "Respond ONLY as a JSON array:\n"
                '[{{"index": 0, "relevance": 85, "supports": true, "explanation": "One sentence in {lang_name}", "key_passage": "Most relevant quote (max 150 chars)"}}, ...]'.format(lang_name=LANG_NAMES.get(lang, "German"))
            ),
            "messages": [{
                "role": "user",
                "content": (
                    f'Statement: "{statement}"\n\n'
                    f'Decisions:\n' + "\n".join(summaries)
                ),
            }],
        },
        timeout=20,
    )

    
    if resp.status_code != 200:
        
        return results

    content = (resp.json().get("content") or [{}])[0].get("text", "")
    
    # Strip markdown code fences if present
    import re
    content = re.sub(r"^```(?:json)?\s*\n?", "", content.strip())
    content = re.sub(r"\n?\s*```\s*$", "", content.strip())
    
    try:
        scores = json.loads(content)
        
    except json.JSONDecodeError as e:
        
        m = re.search(r"\[[\s\S]*\]", content)
        if m:
            try:
                scores = json.loads(m.group())
                
            except json.JSONDecodeError:
                return results
        else:
            return results

    # Merge scores into results
    score_map = {s["index"]: s for s in scores if isinstance(s, dict) and "index" in s}
    
    scored = []
    for i, r in enumerate(results[:12]):
        s = score_map.get(i, {})
        r["_relevance"] = s.get("relevance", 0)
        r["_supports"] = s.get("supports", False)
        r["_explanation"] = s.get("explanation", "")
        r["_key_passage"] = s.get("key_passage", "")
        scored.append(r)

    # Sort by relevance, supporting first
    scored.sort(key=lambda x: (-int(x.get("_supports", False)), -x.get("_relevance", 0)))
    return scored

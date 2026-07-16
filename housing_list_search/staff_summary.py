"""
staff_summary.py — Staff Summary deep module.

Owns staff-facing markdown *bodies*: daily_summary (open units vs waitlist
enrollment, contacts, Needs Review / integrity sections) and proposed_prune.

Staff Publish owns *policy* (partial vs full, when to rewrite baselines).
Call Staff Summary through ``render_staff_summary`` / ``write_proposed_prune``
— not via a bag of unrelated formatters.

Formerly outputs.py.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from housing_list_search.coverage import classify_record_kind, summarize_coverage
from housing_list_search.needs_review import RunReview

PARTIAL_DAILY_SUMMARY_PATH = "daily_summary_partial.md"
STAFF_DAILY_SUMMARY_PATH = "daily_summary.md"
PROPOSED_PRUNE_PATH = "proposed_prune.md"
OPEN_LISTING_DISPLAY_CAP = 100


def _listing_is_open(listing: dict) -> bool:
    """True when units appear available / applications accepted for placement now (#244).

    Waitlist *enrollment* (vendor "Waitlist Open") is **not** unit-available —
    use ``_listing_is_waitlist_enrolling`` for that. Contact-directory waitlist
    cards remain excluded (#1107).
    """
    listing_status = (listing.get("listing_status") or "").lower().strip()
    status_val = (listing.get("status") or "").lower().strip()
    notes_val = (listing.get("notes") or "").lower()

    # Contact-directory / COMING SOON BMR flyers are inventory, not openings.
    if listing_status == "coming_soon":
        return False
    if "contact directory" in notes_val or "waitlist via on-site property manager" in notes_val:
        return False
    # Explicit waitlist language is not "unit available now" (#244)
    if _listing_is_waitlist_enrolling(listing):
        return False

    if listing_status == "open" or status_val == "open":
        return True
    if "accepting applications" in notes_val:
        return True
    # Bare listing_status=waitlist without open/accepting language is not enough
    return False


def _listing_is_waitlist_enrolling(listing: dict) -> bool:
    """True when a waitlist is accepting names — not the same as a vacant unit (#244)."""
    listing_status = (listing.get("listing_status") or "").lower().strip()
    status_val = (listing.get("status") or "").lower().strip()
    notes_val = (listing.get("notes") or "").lower()

    if listing_status == "coming_soon":
        return False
    if "contact directory" in notes_val or "waitlist via on-site property manager" in notes_val:
        return False
    if status_val == "waitlist open" or "waitlist open" in notes_val:
        return True
    # MidPen-style: listing_status waitlist + status Waitlist Open already covered
    if listing_status == "waitlist" and "open" in status_val and "waitlist" in status_val:
        return True
    return False


def _contact_lines(listing: dict) -> list[str]:
    """Phone/email/admin contact lines for staff summary cards (#244)."""
    lines: list[str] = []
    phone = (listing.get("phone") or "").strip()
    email = (listing.get("email") or "").strip()
    admin_phone = (listing.get("administrator_phone") or "").strip()
    admin_contact = (listing.get("administrator_contact") or "").strip()
    admin = (listing.get("administrator") or "").strip()

    # Prefer explicit fields; fall back to notes "phone:" / "email:" crumbs
    notes = listing.get("notes") or ""
    if not phone and "phone:" in notes.lower():
        for part in notes.split("|"):
            p = part.strip()
            if p.lower().startswith("phone:"):
                phone = p.split(":", 1)[-1].strip()
                break
    if not email and "email:" in notes.lower():
        for part in notes.split("|"):
            p = part.strip()
            if p.lower().startswith("email:"):
                email = p.split(":", 1)[-1].strip()
                break

    if phone:
        lines.append(f"Phone: {phone}")
    if email:
        lines.append(f"Email: {email}")
    if admin and (admin_phone or admin_contact):
        bits = [admin]
        if admin_contact:
            bits.append(admin_contact)
        if admin_phone:
            bits.append(admin_phone)
        lines.append(f"Administrator: {', '.join(bits)}")
    elif admin_phone or admin_contact:
        bits = [b for b in (admin_contact, admin_phone) if b]
        lines.append(f"Contact: {', '.join(bits)}")
    return lines


def _write_listing_card(f, listing: dict) -> None:
    """One staff-facing property card (name, status, contacts, link)."""
    name = listing["property_name"][:85] + (
        "..." if len(listing["property_name"]) > 85 else ""
    )
    f.write(f"**{name}**\n")
    f.write(f"Deadline: {listing.get('deadline') or 'None listed'}\n")
    addr = listing.get("address") or ""
    if addr:
        f.write(f"Address: {addr}\n")
    br = listing.get("unit_types") or listing.get("bedrooms") or ""
    if br:
        f.write(f"Units/BR: {br}\n")
    status = listing.get("status") or ""
    if status:
        f.write(f"Status: {status}\n")
    for line in _contact_lines(listing):
        f.write(f"{line}\n")
    f.write(f"Source: {listing['authority']}\n")
    link = (
        listing.get("url")
        or listing.get("source_url")
        or listing.get("document_url")
        or listing.get("flyer_url")
        or ""
    )
    f.write(f"Link: {link}\n\n")


def _listing_is_summary_candidate(listing: dict) -> bool:
    """Staff open list: property inventory only (#989 — portals stay in DB/coverage)."""
    if classify_record_kind(listing) != "property":
        return False
    name = listing.get("property_name", "")
    name_lower = name.lower()
    nav_prefixes = [
        "quick links",
        "skip to",
        "home /",
        "your city /",
        "in this section",
        "select this as",
        "housing open side",
        "/ your city",
    ]
    if "closed" in name_lower:
        return False
    if any(name_lower.startswith(x) for x in nav_prefixes):
        return False
    is_structured = bool(listing.get("source") and ":" in str(listing.get("source", "")))
    return is_structured or len(name) > 4


def _format_run_status(run_stats: dict | None) -> str:
    if not run_stats:
        return ""

    attempted = int(run_stats.get("targets_attempted") or 0)
    failed = list(run_stats.get("failed_authorities") or [])
    succeeded = int(run_stats.get("targets_succeeded") or max(attempted - len(failed), 0))

    lines = ["## Run Status\n\n"]
    if failed:
        lines.append(
            f"- **Targets:** {succeeded} succeeded, {len(failed)} failed (of {attempted} attempted)\n"
        )
        lines.append(f"- **Failed targets:** {', '.join(failed)}\n")
        lines.append(
            "- Review `diff.csv` for `SCRAPE_FAILED` rows — scrape errors, not confirmed closures.\n"
        )
    else:
        lines.append(f"- **Targets:** {succeeded} succeeded (of {attempted} attempted)\n")
    lines.append("\n")
    return "".join(lines)


def _format_needs_review(review: RunReview) -> str:
    suspicious = review.suspicious_zero_authorities
    reverification = review.reverification_due_authorities
    low_yield = review.low_yield
    if not suspicious and not reverification and not low_yield:
        return ""

    lines = ["## Needs Review\n\n"]
    if suspicious:
        lines.append(
            f"- **Suspicious zero:** {len(suspicious)} property-inventory target(s) "
            "returned no property records this run\n"
        )
        lines.append(f"- **Authorities:** {', '.join(suspicious)}\n")
        lines.append(
            "- This is not a confirmed closure — the adapter may have broken, the source "
            "may have changed, or the inventory may genuinely be empty. Review the source "
            "and mark a Validated Zero in TARGETS.md when appropriate (ADR-0003).\n"
        )
    if low_yield:
        # #242: thin portfolios are the soft-partial smoke alarm operators must see
        lines.append(
            f"- **Low-yield:** {len(low_yield)} inventory target(s) returned fewer "
            "property records than expected (possible silent partial scrape)\n"
        )
        detail = ", ".join(f"{a} ({n})" for a, n in low_yield)
        lines.append(f"- **Authorities:** {detail}\n")
        lines.append(
            "- Unconfirmed prior inventory is labelled SCRAPE_FAILED for these "
            "authorities (not REMOVED). Re-check the source before treating missing "
            "rows as closures.\n"
        )
    if reverification:
        lines.append(
            f"- **Reverification due:** {len(reverification)} Validated Zero(s) past "
            "review date in TARGETS.md\n"
        )
        lines.append(f"- **Authorities:** {', '.join(reverification)}\n")
        lines.append(
            "- Re-confirm the source is still empty, update the Validated Zero dates, "
            "or remove the metadata if inventory has returned.\n"
        )
    lines.append("\n")
    return "".join(lines)


def _format_integrity_summary(review: RunReview) -> str:
    stale_n = review.stale_n
    scrape_failed_n = review.scrape_failed_n
    if not stale_n and not scrape_failed_n:
        return ""

    lines = ["## Integrity signals (diff.csv)\n\n"]
    if stale_n:
        lines.append(f"- **STALE:** {stale_n} record(s) not confirmed this run\n")
    if scrape_failed_n:
        lines.append(
            f"- **SCRAPE_FAILED:** {scrape_failed_n} record(s) from failed authority scrapes "
            "(not confirmed closures)\n"
        )
    if stale_n >= review.stale_warn_threshold:
        lines.append(
            "- Review `diff.csv`, then prune when appropriate: "
            "`python scripts/db_manage.py prune --not-seen-since 45`\n"
        )
    lines.append("\n")
    return "".join(lines)


def _format_coverage_summary(listings) -> str:
    cov = summarize_coverage(listings)
    if cov.total == 0:
        return ""

    lines = ["## Coverage breakdown\n\n"]
    lines.append(
        f"- **Property inventory:** {cov.property_count} (per-property or per-unit records)\n"
    )
    if cov.portal_count:
        lines.append(
            f"- **Portal pointers:** {cov.portal_count} "
            f"(registration/notification entry points — not unit lists)\n"
        )
    if cov.program_count:
        lines.append(
            f"- **Program extracts:** {cov.program_count} "
            f"(program-level PDF/page text — not named properties)\n"
        )
    lines.append(
        f"- **UEO-style property count:** {cov.property_count} "
        f"(excludes portals and program noise)\n\n"
    )

    if cov.portal_records:
        lines.append("### Portal pointers (not property inventory)\n\n")
        for rec in cov.portal_records:
            auth = rec.get("authority") or "Unknown"
            link = rec.get("url") or rec.get("administrator_url") or rec.get("source_url") or ""
            lines.append(f"- **{auth}** — register via HouseKeys")
            if link:
                lines.append(f" ({link})")
            lines.append("\n")
        lines.append("\n")

    return "".join(lines)


def generate_daily_summary(
    listings,
    skipped_targets=None,
    *,
    output_path=STAFF_DAILY_SUMMARY_PATH,
    run_stats=None,
    run_review: RunReview | None = None,
):
    skipped_targets = skipped_targets or []
    run_review = run_review or RunReview()
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# 🏠 Santa Clara County Housing Waitlist Summary\n")
        f.write(f"**Run:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
        f.write(_format_run_status(run_stats))
        f.write(_format_needs_review(run_review))
        f.write(_format_integrity_summary(run_review))
        f.write(_format_coverage_summary(listings))

        seen_open: set[tuple] = set()
        seen_waitlist: set[tuple] = set()
        unique_opens: list[dict] = []
        unique_waitlists: list[dict] = []

        for listing in listings:
            name_key = listing.get("property_name", "")[:55].lower().strip()
            key = (name_key, listing.get("authority"))
            if not _listing_is_summary_candidate(listing):
                continue
            if _listing_is_open(listing):
                if key not in seen_open:
                    seen_open.add(key)
                    unique_opens.append(listing)
            elif _listing_is_waitlist_enrolling(listing):
                if key not in seen_waitlist:
                    seen_waitlist.add(key)
                    unique_waitlists.append(listing)

        open_count = len(unique_opens)
        waitlist_count = len(unique_waitlists)
        cov = summarize_coverage(listings)
        f.write(
            f"**Records this run:** {cov.total} extracted "
            f"({cov.property_count} property inventory"
        )
        if cov.portal_count:
            f.write(f", {cov.portal_count} portal pointer{'s' if cov.portal_count != 1 else ''}")
        if cov.program_count:
            f.write(f", {cov.program_count} program extract{'s' if cov.program_count != 1 else ''}")
        f.write(")")
        bits: list[str] = []
        if open_count:
            bits.append(f"{open_count} open / accepting applications (units)")
        if waitlist_count:
            bits.append(f"{waitlist_count} waitlist(s) accepting enrollment")
        if bits:
            f.write(" · " + " · ".join(bits) + "\n\n")
        else:
            f.write("\n\n")

        if unique_opens:
            f.write("## 🔥 CURRENTLY OPEN / ACCEPTING APPLICATIONS\n\n")
            f.write(
                "_Units or applications available now — not waitlist-only enrollment._\n\n"
            )
            display = unique_opens[:OPEN_LISTING_DISPLAY_CAP]
            for listing in display:
                _write_listing_card(f, listing)
            if open_count > len(display):
                remaining = open_count - len(display)
                f.write(
                    f"_+ {remaining} more open listing(s) in this run — "
                    "filter `current_full.csv` for open/accepting status._\n\n"
                )

        if unique_waitlists:
            f.write("## 📋 WAITLISTS ACCEPTING ENROLLMENT\n\n")
            f.write(
                "_Waitlist is open for names — **not** the same as a vacant unit available "
                "today. Confirm with the property before telling a client to apply for housing._\n\n"
            )
            display_wl = unique_waitlists[:OPEN_LISTING_DISPLAY_CAP]
            for listing in display_wl:
                _write_listing_card(f, listing)
            if waitlist_count > len(display_wl):
                remaining = waitlist_count - len(display_wl)
                f.write(f"_+ {remaining} more waitlist listing(s) in this run._\n\n")

        if not unique_opens and not unique_waitlists:
            if cov.total > 0:
                f.write(
                    "**No open units or enrolling waitlists in this run.** "
                    f"The {cov.total} record(s) extracted are closed, registration portals, "
                    "static inventory without status, or otherwise not actionable for placement.\n\n"
                )
            else:
                f.write("**No listings extracted in this run.**\n\n")

        f.write("## 📊 Full Dataset for Import\n")
        f.write(
            "- `current_full.csv` — full DB snapshot (all ever-seen rows). "
            "Filter `confirmed_this_run=Y` for inventory confirmed this run; "
            "`record_kind=property` for UEO-style property rows only.\n"
        )
        f.write(
            "- `diff.csv` — this run's delta: NEW / UPDATED / STALE / SCRAPE_FAILED rows (use for incremental imports)\n"
        )
        f.write(
            "- `changelog_diffs.md` / `changelog_diffs.csv` — human/machine changelog vs last run\n\n"
        )
        f.write("**Note:** Some city sites block automated access.\n")
        f.write("\nReady for internal tech mailing list.\n")

        # Human-readable report of intentionally skipped targets (never in CSV)
        if skipped_targets:
            f.write("\n## ⚠️  Intentionally Skipped Targets (no_public_list)\n\n")
            f.write(
                "These targets are documented in TARGETS.md with the `no_public_list` marker.\n"
            )
            f.write(
                "They are skipped automatically to avoid wasting research effort on cities without\n"
            )
            f.write(
                "public structured BMR lists, waitlists, or extractable portals. When a usable public\n"
            )
            f.write(
                "source appears, a human removes the marker and the target becomes active again.\n\n"
            )
            for auth, note in skipped_targets:
                f.write(f"- **{auth}**\n")
                if note:
                    f.write(f"  Notes: {note}\n")
                f.write("\n")

    print(f"✅ Generated clean, deduplicated {output_path}")


def write_proposed_prune(
    *,
    run_id: str,
    stale_n: int,
    scrape_failed_n: int = 0,
    diff_path: str = "diff.csv",
    output_path: str = PROPOSED_PRUNE_PATH,
) -> str | None:
    """Write a short operator artifact with prune guidance after a full run (#240).

    Never deletes inventory — only documents how many STALE rows are candidates
    and the exact dry-run / apply commands. SCRAPE_FAILED is called out so
    operators do not prune failures as closures.
    """
    lines = [
        "# Proposed prune\n\n",
        f"Generated after full run `{run_id}` at {datetime.now().isoformat(timespec='seconds')}.\n\n",
        "This file is advisory only. **Nothing is deleted until you run a prune command.**\n\n",
        "## Counts\n\n",
        f"- **STALE** (not confirmed this run — candidates after review): **{stale_n}**\n",
        f"- **SCRAPE_FAILED** (authority/machinery failed — **do not prune as gone**): "
        f"**{scrape_failed_n}**\n\n",
    ]
    if stale_n <= 0:
        lines.append(
            "## Action\n\n"
            "No STALE rows this run. No prune needed.\n"
        )
    else:
        lines.extend(
            [
                "## Recommended commands\n\n",
                "1. Review candidates in the machine delta:\n\n",
                f"   `grep '^STALE,' {diff_path} | head`\n\n",
                "2. Dry-run prune from this run's STALE rows (preferred):\n\n",
                f"   `python scripts/db_manage.py prune --from-diff --diff-path {diff_path} --dry-run`\n\n",
                "3. Apply only after review:\n\n",
                f"   `python scripts/db_manage.py prune --from-diff --diff-path {diff_path}`\n\n",
                "4. Age-based prune only when intentional (not the default):\n\n",
                "   `python scripts/db_manage.py prune --not-seen-since 45 --dry-run`\n\n",
                "Avoid `--all-stale` unless you mean to wipe everything unconfirmed.\n",
            ]
        )
    text = "".join(lines)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"✅ Wrote {output_path} (STALE={stale_n}, SCRAPE_FAILED={scrape_failed_n})")
    return output_path


def render_staff_summary(
    listings: list[dict[str, Any]],
    *,
    skipped_targets: list[tuple[str, str]] | None = None,
    daily_summary_path: str = STAFF_DAILY_SUMMARY_PATH,
    run_stats: dict[str, Any] | None = None,
    run_review: RunReview | None = None,
    proposed_prune: dict[str, Any] | None = None,
) -> None:
    """Single Staff Summary interface: daily markdown (+ optional prune note).

    Staff Publish decides *whether* and *which paths*; this module owns the body.
    ``proposed_prune`` keys: run_id, stale_n, scrape_failed_n, diff_path (optional).
    Omit or pass None to skip proposed_prune.md (partial runs).
    """
    generate_daily_summary(
        listings,
        skipped_targets=skipped_targets,
        output_path=daily_summary_path,
        run_stats=run_stats,
        run_review=run_review,
    )
    if not proposed_prune:
        return
    write_proposed_prune(
        run_id=str(proposed_prune.get("run_id") or ""),
        stale_n=int(proposed_prune.get("stale_n") or 0),
        scrape_failed_n=int(proposed_prune.get("scrape_failed_n") or 0),
        diff_path=str(proposed_prune.get("diff_path") or "diff.csv"),
    )

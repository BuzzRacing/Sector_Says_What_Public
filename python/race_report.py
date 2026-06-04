"""AI Race Report generator — F1 journalist-style post-race recap.

Lazy-generated on first request, then cached in races.race_report.
"""
import race_db
import openai_commentary


def _format_lap(secs):
    """Format seconds as m:ss.sss or ss.sss."""
    if not secs or secs <= 0:
        return "—"
    m = int(secs // 60)
    s = secs - m * 60
    return f"{m}:{s:06.3f}" if m else f"{s:.3f}"


def _lic_letter(level):
    if not level or level <= 0:
        return "?"
    if level >= 18:
        return "A"
    if level >= 14:
        return "B"
    if level >= 10:
        return "C"
    if level >= 6:
        return "D"
    return "R"


def _build_prompt(detail: dict) -> str:
    """Assemble the data prompt for the race report AI call."""
    race = detail["race"]
    drivers = detail["drivers"]
    commentary = detail["commentary"]
    incidents = detail["incidents"]

    lines = []

    # Race header
    track = race.get("track_name") or "Unknown Track"
    config = race.get("track_config") or ""
    laps = race.get("total_laps") or "?"
    cars = race.get("car_count") or len(drivers)
    winner = race.get("winner_name") or "Unknown"
    date = race.get("race_date") or ""
    dur = race.get("duration_s")
    dur_str = f"{int(dur // 60)}m {int(dur % 60)}s" if dur else "?"

    lines.append(f"RACE: {track}" + (f" — {config}" if config else ""))
    lines.append(f"Date: {date} | Laps: {laps} | Cars: {cars} | Duration: {dur_str}")
    lines.append(f"Winner: {winner}")
    lines.append("")

    # DOTD — races.dotd_score is already the winner's vote-share percent
    dotd_name = race.get("dotd_name")
    dotd_pct = race.get("dotd_score")
    if dotd_name:
        lines.append(f"DRIVER OF THE DAY: {dotd_name} ({dotd_pct:.1f}% of the vote)" if dotd_pct else f"DRIVER OF THE DAY: {dotd_name}")
    lines.append("")

    # Classification (top 10 + owner)
    lines.append("FINAL CLASSIFICATION:")
    lines.append("Pos | Driver | #Car | Lic | iR | Grid | Best Lap | Laps Led | Inc | DOTD %")
    lines.append("-" * 80)
    owner = None
    for d in drivers[:10]:
        fp = d.get("finish_position") or "?"
        name = d.get("driver_name") or "?"
        num = d.get("car_number") or "?"
        lic = _lic_letter(d.get("license_level"))
        sr = d.get("license_sub_level")
        sr_str = f"{sr / 100:.2f}" if sr else "?"
        ir = d.get("irating") or "?"
        gp = d.get("grid_position") or "?"
        bl = _format_lap(d.get("best_lap"))
        ll = d.get("laps_led") or 0
        inc = d.get("incidents") or 0
        dotd = d.get("dotd_percent") or 0
        lines.append(f"P{fp} | {name} | #{num} | {lic} {sr_str} | {ir} | P{gp} | {bl} | {ll} | {inc} | {dotd:.1f}%")
        if d.get("is_owner"):
            owner = d

    # Owner outside top 10
    if not owner:
        for d in drivers[10:]:
            if d.get("is_owner"):
                owner = d
                fp = d.get("finish_position") or "?"
                name = d.get("driver_name") or "?"
                num = d.get("car_number") or "?"
                gp = d.get("grid_position") or "?"
                bl = _format_lap(d.get("best_lap"))
                inc = d.get("incidents") or 0
                dotd = d.get("dotd_percent") or 0
                lines.append(f"... P{fp} | {name} (FOCUS DRIVER) | #{num} | P{gp} | {bl} | {inc} | {dotd:.1f}%")
                break

    if owner:
        name = owner.get("driver_name") or "Focus Driver"
        gp = owner.get("grid_position") or "?"
        fp = owner.get("finish_position") or "?"
        gain = (gp - fp) if isinstance(gp, int) and isinstance(fp, int) else 0
        inc = owner.get("incidents") or 0
        bl = _format_lap(owner.get("best_lap"))
        dotd = owner.get("dotd_score") or 0
        lines.append("")
        gain_text = (
            f"gained {gain} places" if gain > 0
            else f"lost {abs(gain)} places" if gain < 0
            else "held position"
        )
        lines.append(
            f"OWNER / FOCUS DRIVER: {name}, started P{gp}, finished P{fp} "
            f"({gain_text}), best lap {bl}, {inc} incidents, DOTD {dotd:.1f}."
        )
        lines.append(
            "REPORT REQUIREMENT: the race report MUST devote at least one "
            "paragraph (or a clearly-labelled mention) to the OWNER / FOCUS "
            "DRIVER above. Cover their grid-to-finish swing, their pace "
            "relative to the leaders, and any notable moments in the KEY "
            "MOMENTS list that involve them. Do not omit them even if the "
            "race-winning storyline is elsewhere — they are the audience."
        )

    lines.append("")

    # Key commentary moments (up to 20, prioritise owner + lead changes)
    if commentary:
        # Score each entry for selection
        scored = []
        for c in commentary:
            score = 0
            trigger = (c.get("trigger_name") or "").lower()
            if c.get("is_owner"):
                score += 5
            if "lead" in trigger:
                score += 4
            if "overtake" in trigger:
                score += 3
            if "fastest" in trigger:
                score += 3
            if "incident" in trigger or "collision" in trigger:
                score += 3
            if "pit" in trigger:
                score += 2
            if "battle" in trigger:
                score += 2
            if "summary" in trigger or "update" in trigger:
                score += 1
            scored.append((score, c))

        scored.sort(key=lambda x: -x[0])
        selected = [c for _, c in scored[:20]]
        # Re-sort by time
        selected.sort(key=lambda c: c.get("timestamp_s") or 0)

        lines.append("KEY MOMENTS (from live commentary):")
        for c in selected:
            t = c.get("timestamp_s") or 0
            mins = int(t // 60)
            secs = int(t % 60)
            trigger = c.get("trigger_name") or "update"
            text = (c.get("ai_text") or "")[:200]
            who = c.get("commentator") or ""
            lines.append(f"  [{mins:02d}:{secs:02d}] {trigger}: {text}" +
                         (f" ({who})" if who else ""))
        lines.append("")

    # Incidents summary
    if incidents:
        lines.append(f"INCIDENTS: {len(incidents)} recorded")
        for inc in incidents[:5]:
            desc = inc.get("description") or inc.get("ai_text") or ""
            if desc:
                lines.append(f"  - {desc[:150]}")
        lines.append("")

    # Biggest mover & biggest loser. Both are story-worthy — race 4
    # had Cherif Abass +11 (mentioned) and Aaron Blaiotta -19 (entirely
    # missed). Surface both so the report writer can choose which to
    # foreground or include both.
    if drivers:
        movers = [d for d in drivers
                  if d.get("grid_position") and d.get("finish_position")]
        if movers:
            best = max(movers, key=lambda d: (d["grid_position"] - d["finish_position"]))
            gain = best["grid_position"] - best["finish_position"]
            if gain > 0:
                lines.append(f"BIGGEST MOVER: {best['driver_name']} — "
                             f"P{best['grid_position']} to P{best['finish_position']} (+{gain}), "
                             f"{best.get('incidents') or 0} incidents")
            worst = min(movers, key=lambda d: (d["grid_position"] - d["finish_position"]))
            loss = worst["grid_position"] - worst["finish_position"]
            if loss < 0 and abs(loss) >= 5:
                # Only call out a "biggest loser" when the drop is
                # meaningful (>=5 places). Tag with incidents/laps to
                # hint at the cause without speculating.
                inc = worst.get("incidents") or 0
                lc = worst.get("laps_complete")
                race_laps = race.get("total_laps") or 0
                dnf_hint = ""
                if isinstance(lc, int) and isinstance(race_laps, int) and race_laps > 0 and lc < race_laps - 2:
                    dnf_hint = f", retired/DNF after {lc}/{race_laps} laps"
                lines.append(
                    f"BIGGEST LOSER: {worst['driver_name']} — "
                    f"P{worst['grid_position']} to P{worst['finish_position']} ({loss}), "
                    f"{inc} incidents{dnf_hint}"
                )
            lines.append("")

    return "\n".join(lines)


def generate_race_report(race_id: int) -> str:
    """Generate (or retrieve cached) race report for a given race_id.

    Returns the report text. Stores it in races.race_report for future calls.
    """
    detail = race_db.get_race_detail(race_id)
    if not detail:
        return "[ERROR] Race not found."

    # Check cache
    cached = detail["race"].get("race_report")
    if cached:
        return cached

    # Build prompt and generate
    prompt = _build_prompt(detail)
    report = openai_commentary.generate_report_text(prompt)

    # Cache in DB
    try:
        conn = race_db._conn()
        conn.execute("UPDATE races SET race_report=? WHERE race_id=?",
                     (report, race_id))
        conn.commit()
    except Exception as e:
        print(f"[REPORT] Failed to cache report: {e}")

    return report


def regenerate_race_report(race_id: int) -> str:
    """Force-regenerate the report (ignores cache)."""
    detail = race_db.get_race_detail(race_id)
    if not detail:
        return "[ERROR] Race not found."

    prompt = _build_prompt(detail)
    report = openai_commentary.generate_report_text(prompt)

    try:
        conn = race_db._conn()
        conn.execute("UPDATE races SET race_report=? WHERE race_id=?",
                     (report, race_id))
        conn.commit()
    except Exception as e:
        print(f"[REPORT] Failed to cache report: {e}")

    return report

"""Hypothesis registry and concepts: learning from outside analysts without inheriting their views.

An analyst's idea enters as a neutrally stated hypothesis (testable, or a historical observation
checked against stored data), with every place it was said recorded as a source mention. Rules
this module enforces, beyond the append-only tables of migration 010:
- Independent support counts distinct creators; one creator repeating an idea adds mentions only.
- Source-blind testing: a hypothesis's wording and alternatives must not name any of its sources,
  and `brief` (what the quant researcher gets) carries no source fields. Its trials must use the
  family `hyp:<id>`, so a status change can be checked against exactly those trials.
- Hindsight: 'forward' evidence must be a prediction recorded after the hypothesis was registered;
  everything else is retrospective. Data-derived evidence cites the CLI command and its data date.
- Concepts are kept for reasoning and have no path to validation.
- Source scenarios are a source's conditional predictions with resolution criteria we wrote; they
  are monitored but never scored as the creator's hit or miss.
Nothing here changes a strategy or decision rule; promotion still goes through `strategies`.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date

from cryptoagent import privacy

KINDS = ("testable", "observation")
DIRECTIONS = ("supports", "contradicts", "neutral")
BASES = ("retrospective", "forward")
OUTCOMES = ("met", "not_met", "unresolvable")
CITE_KINDS = ("trial", "prediction", "claim", "url", "command")
MAX_QUOTE_WORDS = 25
SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
# Status changes that must point at this hypothesis's own trials, and the period those trials ran on.
TRIAL_STATUSES = {"train_supported": "train", "train_failed": "train",
                  "validated": "validation", "validation_failed": "validation"}
RETROSPECTIVE_STATUSES = ("consistent", "inconsistent", "mixed")
STOPWORDS = {"the", "and", "for", "with", "that", "than", "this", "when", "after", "before", "from", "into", "its",
             "are", "was", "has", "have", "more", "less", "not", "then", "over", "under", "each", "any", "all"}


class RegistryError(ValueError):
    pass


def _today() -> str:
    return date.today().isoformat()


def _date(value: str, name: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        raise RegistryError(f"{name} must be YYYY-MM-DD, got {value!r}") from None


def _slug(value: str) -> str:
    if not SLUG.match(value or ""):
        raise RegistryError(f"slug {value!r} must be lowercase words joined by hyphens, e.g. weekly-pivot-failure")
    return value


def _require(**fields: str | None) -> None:
    missing = [name for name, value in fields.items() if not (value or "").strip()]
    if missing:
        raise RegistryError(f"required: {', '.join(missing)}")


def _hypothesis(conn: sqlite3.Connection, hypothesis_id: int) -> dict:
    row = conn.execute("SELECT * FROM hypotheses WHERE id = ?", (hypothesis_id,)).fetchone()
    if not row:
        raise RegistryError(f"hypothesis {hypothesis_id} does not exist")
    return dict(row)


def status(conn: sqlite3.Connection, hypothesis_id: int) -> str | None:
    row = conn.execute("SELECT status FROM hypothesis_events WHERE hypothesis_id = ? ORDER BY id DESC LIMIT 1",
                       (hypothesis_id,)).fetchone()
    return row[0] if row else None


def _creators(conn: sqlite3.Connection, hypothesis_id: int) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT creator FROM source_mentions WHERE hypothesis_id = ?",
                                       (hypothesis_id,))]


def _names_in(text: str, creators: list[str]) -> list[str]:
    """Creators whose full name, or last name part (4+ letters, e.g. a surname), appears in the text."""
    lowered = text.lower()
    found = []
    for creator in creators:
        words = re.findall(r"[a-z]+", creator.lower())
        parts = [creator.lower()] + ([words[-1]] if len(words) > 1 and len(words[-1]) >= 4 else [])
        if any(re.search(rf"\b{re.escape(p)}\b", lowered) for p in parts):
            found.append(creator)
    return found


def _check_blind(text: str, creators: list[str], what: str) -> None:
    if leaked := _names_in(text, creators):
        raise RegistryError(f"{what} names its source ({', '.join(leaked)}); state it neutrally for blind testing")


def add_hypothesis(conn: sqlite3.Connection, *, slug: str, kind: str, subject: str, statement: str, conditions: str,
                   required_data: str, methodology: str, created_by: str, refines_id: int | None = None) -> dict:
    _require(statement=statement, conditions=conditions, required_data=required_data, methodology=methodology,
             subject=subject)
    if kind not in KINDS:
        raise RegistryError(f"kind must be one of {', '.join(KINDS)}")
    if created_by not in ("agent", "user"):
        raise RegistryError("created_by must be agent or user")
    privacy.check(statement, conditions, required_data, methodology)
    if refines_id is not None:
        _hypothesis(conn, refines_id)
    with conn:
        hid = conn.execute(
            "INSERT INTO hypotheses (slug, kind, subject, statement, conditions, required_data, methodology,"
            " refines_id, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (_slug(slug), kind, subject.strip().upper(), statement.strip(), conditions.strip(), required_data.strip(),
             methodology.strip(), refines_id, created_by)).lastrowid
        conn.execute("INSERT INTO hypothesis_events (hypothesis_id, status, decided_by) VALUES (?, 'registered',"
                     " 'system')", (hid,))
    return {"hypothesis_id": hid, "status": "registered", "test_family": f"hyp:{hid}"}


def add_concept(conn: sqlite3.Connection, *, slug: str, name: str, summary: str, usage: str, limits: str,
                created_by: str, supersedes_id: int | None = None) -> dict:
    _require(name=name, summary=summary, usage=usage, limits=limits)
    if created_by not in ("agent", "user"):
        raise RegistryError("created_by must be agent or user")
    privacy.check(name, summary, usage, limits)
    if supersedes_id is not None and not conn.execute("SELECT 1 FROM concepts WHERE id = ?",
                                                      (supersedes_id,)).fetchone():
        raise RegistryError(f"concept {supersedes_id} does not exist")
    with conn:
        cid = conn.execute("INSERT INTO concepts (slug, name, summary, usage, limits, supersedes_id, created_by)"
                           " VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (_slug(slug), name.strip(), summary.strip(), usage.strip(), limits.strip(), supersedes_id,
                            created_by)).lastrowid
    return {"concept_id": cid}


def add_mention(conn: sqlite3.Connection, *, creator: str, url: str, published_on: str, date_basis: str,
                recorded_by: str, hypothesis_id: int | None = None, concept_id: int | None = None,
                quote: str | None = None) -> dict:
    """Record that a source stated this idea. A repeat by the same creator is a mention, not support."""
    _require(creator=creator, date_basis=date_basis)
    if (hypothesis_id is None) == (concept_id is None):
        raise RegistryError("a mention belongs to exactly one hypothesis or one concept")
    if not url.startswith(("http://", "https://")):
        raise RegistryError(f"url {url!r} must start with http(s)://")
    published = _date(published_on, "published_on")
    if published > _today():
        raise RegistryError("published_on is in the future")
    if quote and len(quote.split()) > MAX_QUOTE_WORDS:
        raise RegistryError(f"quote is longer than {MAX_QUOTE_WORDS} words")
    privacy.check(quote, date_basis)
    creator = creator.strip()
    if hypothesis_id is not None:
        h = _hypothesis(conn, hypothesis_id)
        alternatives = " ".join(r[0] + " " + r[1] for r in conn.execute(
            "SELECT explanation, benchmark FROM hypothesis_alternatives WHERE hypothesis_id = ?", (hypothesis_id,)))
        _check_blind(" ".join([h["slug"], h["statement"], h["conditions"], h["required_data"], h["methodology"],
                               alternatives]), [creator], f"hypothesis {hypothesis_id}")
        repeat = creator in _creators(conn, hypothesis_id)
    else:
        if not conn.execute("SELECT 1 FROM concepts WHERE id = ?", (concept_id,)).fetchone():
            raise RegistryError(f"concept {concept_id} does not exist")
        repeat = bool(conn.execute("SELECT 1 FROM source_mentions WHERE concept_id = ? AND creator = ?",
                                   (concept_id, creator)).fetchone())
    with conn:
        mid = conn.execute(
            "INSERT INTO source_mentions (hypothesis_id, concept_id, creator, url, published_on, date_basis, quote,"
            " recorded_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (hypothesis_id, concept_id, creator, url, published, date_basis.strip(), quote, recorded_by)).lastrowid
    column, ref = ("hypothesis_id", hypothesis_id) if hypothesis_id is not None else ("concept_id", concept_id)
    mentions, independent = conn.execute(f"SELECT count(*), count(DISTINCT creator) FROM source_mentions"
                                         f" WHERE {column} = ?", (ref,)).fetchone()
    return {"mention_id": mid, "mentions": mentions, "independent_sources": independent,
            "repeat_by_same_creator": repeat}


def add_alternative(conn: sqlite3.Connection, hypothesis_id: int, explanation: str, benchmark: str) -> dict:
    _require(explanation=explanation, benchmark=benchmark)
    _hypothesis(conn, hypothesis_id)
    privacy.check(explanation, benchmark)
    _check_blind(f"{explanation} {benchmark}", _creators(conn, hypothesis_id), "the alternative")
    with conn:
        aid = conn.execute("INSERT INTO hypothesis_alternatives (hypothesis_id, explanation, benchmark)"
                           " VALUES (?, ?, ?)", (hypothesis_id, explanation.strip(), benchmark.strip())).lastrowid
    return {"alternative_id": aid}


def add_evidence(conn: sqlite3.Connection, hypothesis_id: int, *, direction: str, basis: str, summary: str,
                 cite: str, created_by: str, data_through: str | None = None) -> dict:
    """cite: trial:<id>, prediction:<id>, claim:<id>, url:https://... or command:<cryptoagent arguments>."""
    h = _hypothesis(conn, hypothesis_id)
    _require(summary=summary)
    if direction not in DIRECTIONS or basis not in BASES:
        raise RegistryError(f"direction is one of {', '.join(DIRECTIONS)}; basis is one of {', '.join(BASES)}")
    kind, _, ref = cite.partition(":")
    if kind not in CITE_KINDS or not ref.strip():
        raise RegistryError(f"cite must look like {', '.join(k + ':...' for k in CITE_KINDS)}")
    if kind in ("trial", "prediction", "claim"):
        table = {"trial": "trials", "prediction": "predictions", "claim": "claims"}[kind]
        row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (int(ref),)).fetchone() if ref.isdigit() else None
        if not row:
            raise RegistryError(f"{kind} {ref} does not exist")
    if kind == "url" and not ref.startswith(("http://", "https://")):
        raise RegistryError(f"url {ref!r} must start with http(s)://")
    if kind == "command":
        if data_through is None:
            raise RegistryError("command evidence needs --data-through: the last data day the command used")
        data_through = _date(data_through, "data_through")
    if basis == "forward":
        # Forward evidence is a prediction made after the idea was registered here, never data the
        # source (or we) could already see.
        if kind != "prediction":
            raise RegistryError("forward evidence must cite a prediction recorded after the hypothesis was registered")
        if row["created_at"] < h["created_at"]:
            raise RegistryError(f"prediction {ref} was recorded before hypothesis {hypothesis_id} was registered, "
                                "so it is retrospective, not forward")
    privacy.check(summary)
    with conn:
        eid = conn.execute(
            "INSERT INTO hypothesis_evidence (hypothesis_id, direction, basis, summary, cite_kind, cite_ref,"
            " data_through, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (hypothesis_id, direction, basis, summary.strip(), kind, ref.strip(), data_through, created_by)).lastrowid
    return {"evidence_id": eid}


def set_status(conn: sqlite3.Connection, hypothesis_id: int, new_status: str, decided_by: str,
               note: str | None = None, trial_ids: list[int] | None = None) -> dict:
    h = _hypothesis(conn, hypothesis_id)
    if decided_by not in ("agent", "user"):
        raise RegistryError("decided_by must be agent or user")
    trial_ids = trial_ids or []
    if new_status in TRIAL_STATUSES:
        period, family = TRIAL_STATUSES[new_status], f"hyp:{hypothesis_id}"
        if not trial_ids:
            raise RegistryError(f"{new_status} needs the {period} trial ids (family {family}) it rests on")
        for tid in trial_ids:
            row = conn.execute("SELECT family, period FROM trials WHERE id = ?", (tid,)).fetchone()
            if not row:
                raise RegistryError(f"trial {tid} does not exist")
            if row["family"] != family or row["period"] != period:
                raise RegistryError(f"trial {tid} is {row['family']}/{row['period']}; {new_status} needs {period} "
                                    f"trials in family {family}")
    if new_status in RETROSPECTIVE_STATUSES and not conn.execute(
            "SELECT 1 FROM hypothesis_evidence WHERE hypothesis_id = ? AND basis = 'retrospective'",
            (hypothesis_id,)).fetchone():
        raise RegistryError(f"{new_status} needs retrospective evidence recorded first (hypothesis evidence)")
    if new_status in ("needs_data", "retired", "monitoring") and not (note or "").strip():
        raise RegistryError(f"{new_status} needs a note saying why")
    privacy.check(note)
    current = status(conn, hypothesis_id)
    if not conn.execute("SELECT 1 FROM hypothesis_transitions WHERE kind = ? AND from_status = ? AND to_status = ?",
                        (h["kind"], current or "", new_status)).fetchone():
        raise RegistryError(f"a {h['kind']} hypothesis cannot go from {current} to {new_status}")
    with conn:
        conn.execute("INSERT INTO hypothesis_events (hypothesis_id, status, decided_by, note, evidence)"
                     " VALUES (?, ?, ?, ?, ?)", (hypothesis_id, new_status, decided_by, note,
                                                 json.dumps({"trials": trial_ids}) if trial_ids else None))
    return {"hypothesis_id": hypothesis_id, "from": current, "status": new_status}


def _trials(conn: sqlite3.Connection, hypothesis_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, period, market, trades, mean_return, created_at FROM trials WHERE family = ? ORDER BY id",
        (f"hyp:{hypothesis_id}",))]


def show(conn: sqlite3.Connection, hypothesis_id: int) -> dict:
    h = _hypothesis(conn, hypothesis_id)
    mentions = [dict(r) for r in conn.execute(
        "SELECT id, creator, url, published_on, date_basis, quote, recorded_at FROM source_mentions"
        " WHERE hypothesis_id = ? ORDER BY published_on, id", (hypothesis_id,))]
    evidence = [dict(r) for r in conn.execute(
        "SELECT id, direction, basis, summary, cite_kind, cite_ref, data_through, created_at FROM hypothesis_evidence"
        " WHERE hypothesis_id = ? ORDER BY id", (hypothesis_id,))]
    tally = {}
    for e in evidence:
        tally.setdefault(e["basis"], {d: 0 for d in DIRECTIONS})[e["direction"]] += 1
    return h | {
        "status": status(conn, hypothesis_id),
        "history": [dict(r) for r in conn.execute(
            "SELECT status, decided_by, note, evidence, created_at FROM hypothesis_events WHERE hypothesis_id = ?"
            " ORDER BY id", (hypothesis_id,))],
        "sources": mentions,
        "independent_sources": len({m["creator"] for m in mentions}),
        "mentions": len(mentions),
        "first_published": mentions[0]["published_on"] if mentions else None,
        "alternatives": [dict(r) for r in conn.execute(
            "SELECT id, explanation, benchmark FROM hypothesis_alternatives WHERE hypothesis_id = ? ORDER BY id",
            (hypothesis_id,))],
        "evidence": evidence,
        "evidence_tally": tally,
        "trials": _trials(conn, hypothesis_id),
        "test_family": f"hyp:{hypothesis_id}",
        "refined_by": [r[0] for r in conn.execute("SELECT id FROM hypotheses WHERE refines_id = ?", (hypothesis_id,))],
        "notes": ["independent_sources counts distinct creators; repeats by one creator are not confirmation",
                  "retrospective evidence covers data the source could already see"],
    }


def brief(conn: sqlite3.Connection, hypothesis_id: int) -> dict:
    """What the quant researcher gets: the idea and its benchmarks, with no source, quote, date or evidence."""
    h = _hypothesis(conn, hypothesis_id)
    out = {
        "hypothesis_id": hypothesis_id, "kind": h["kind"], "subject": h["subject"], "statement": h["statement"],
        "conditions": h["conditions"], "required_data": h["required_data"], "methodology": h["methodology"],
        "alternatives": [{"explanation": r[0], "benchmark": r[1]} for r in conn.execute(
            "SELECT explanation, benchmark FROM hypothesis_alternatives WHERE hypothesis_id = ? ORDER BY id",
            (hypothesis_id,))],
        "family": f"hyp:{hypothesis_id}",
        "trials_already_run": _trials(conn, hypothesis_id),
        "rules": ["test against the benchmarks as well as all-days drift", "train first; validation only if train "
                  "supports it, unchanged", "never run the test period"],
    }
    _check_blind(json.dumps(out), _creators(conn, hypothesis_id), "the brief")
    return out


def list_hypotheses(conn: sqlite3.Connection, status_filter: str | None = None, kind: str | None = None) -> list[dict]:
    rows = conn.execute(
        "SELECT h.id, h.slug, h.kind, h.subject, h.statement, h.created_at,"
        " (SELECT count(*) FROM source_mentions WHERE hypothesis_id = h.id) AS mentions,"
        " (SELECT count(DISTINCT creator) FROM source_mentions WHERE hypothesis_id = h.id) AS independent_sources,"
        " (SELECT count(*) FROM trials WHERE family = 'hyp:' || h.id) AS trials"
        " FROM hypotheses h WHERE ? IS NULL OR h.kind = ? ORDER BY h.id", (kind, kind)).fetchall()
    out = [dict(r) | {"status": status(conn, r["id"])} for r in rows]
    return [r for r in out if not status_filter or r["status"] == status_filter]


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 3 and w not in STOPWORDS}


def similar(conn: sqlite3.Connection, text: str, limit: int = 5) -> list[dict]:
    """Candidate duplicates by word overlap (Jaccard). Deciding whether two ideas are the same is judgment."""
    query = _tokens(text)
    found = []
    for r in conn.execute("SELECT id, slug, statement FROM hypotheses"):
        found.append(("hypothesis", r["id"], r["slug"], r["statement"], _tokens(r["slug"] + " " + r["statement"])))
    for r in conn.execute("SELECT id, slug, name, summary FROM concepts"):
        found.append(("concept", r["id"], r["slug"], r["name"], _tokens(f"{r['slug']} {r['name']} {r['summary']}")))
    scored = [{"type": t, "id": i, "slug": s, "text": x, "overlap": round(len(query & w) / len(query | w), 3)}
              for t, i, s, x, w in found if query | w]
    return sorted([s for s in scored if s["overlap"] > 0], key=lambda s: -s["overlap"])[:limit]


def list_concepts(conn: sqlite3.Connection) -> list[dict]:
    rows = [dict(r) for r in conn.execute("SELECT * FROM concepts c WHERE NOT EXISTS"
                                          " (SELECT 1 FROM concepts n WHERE n.supersedes_id = c.id) ORDER BY id")]
    for r in rows:
        r["sources"] = [dict(m) for m in conn.execute(
            "SELECT creator, url, published_on FROM source_mentions WHERE concept_id = ? ORDER BY published_on",
            (r["id"],))]
        r["note"] = "a framework for reasoning, not a validated rule"
    return rows


def add_scenario(conn: sqlite3.Connection, *, creator: str, url: str, published_on: str, date_basis: str,
                 description: str, resolution: str, deadline: str, recorded_by: str,
                 hypothesis_id: int | None = None) -> dict:
    _require(creator=creator, date_basis=date_basis, description=description, resolution=resolution)
    if not url.startswith(("http://", "https://")):
        raise RegistryError(f"url {url!r} must start with http(s)://")
    published, due = _date(published_on, "published_on"), _date(deadline, "deadline")
    if due <= published:
        raise RegistryError("deadline must be after published_on")
    if hypothesis_id is not None:
        _hypothesis(conn, hypothesis_id)
    privacy.check(description, resolution)
    with conn:
        sid = conn.execute(
            "INSERT INTO source_scenarios (creator, url, published_on, date_basis, description, resolution, deadline,"
            " hypothesis_id, recorded_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (creator.strip(), url, published, date_basis.strip(), description.strip(), resolution.strip(), due,
             hypothesis_id, recorded_by)).lastrowid
    return {"scenario_id": sid, "note": "resolution criteria are our interpretation; never scored as the creator's"}


def resolve_scenario(conn: sqlite3.Connection, scenario_id: int, outcome: str, note: str, cite: str,
                     resolved_by: str) -> dict:
    _require(note=note, cite=cite)
    if outcome not in OUTCOMES:
        raise RegistryError(f"outcome must be one of {', '.join(OUTCOMES)}")
    row = conn.execute("SELECT deadline FROM source_scenarios WHERE id = ?", (scenario_id,)).fetchone()
    if not row:
        raise RegistryError(f"scenario {scenario_id} does not exist")
    privacy.check(note)
    with conn:
        conn.execute("INSERT INTO scenario_outcomes (scenario_id, outcome, note, cite, resolved_by) VALUES (?, ?, ?, ?, ?)",
                     (scenario_id, outcome, note.strip(), cite.strip(), resolved_by))
    return {"scenario_id": scenario_id, "outcome": outcome}


def list_scenarios(conn: sqlite3.Connection, creator: str | None = None) -> list[dict]:
    rows = conn.execute(
        "SELECT s.*, o.outcome, o.note AS outcome_note, o.cite AS outcome_cite, o.resolved_at FROM source_scenarios s"
        " LEFT JOIN scenario_outcomes o ON o.scenario_id = s.id WHERE ? IS NULL OR s.creator = ? ORDER BY s.id",
        (creator, creator)).fetchall()
    return [dict(r) | {"due": dict(r)["outcome"] is None and dict(r)["deadline"] <= _today(),
                       "scored_as_creator": False} for r in rows]

"""Read-only spare parts and warehouse catalog used by the MCP server.

Every public function returns ``{"count": n, "results": [...]}`` where each result
has ``id``, ``title`` and ``content`` (a self-contained text summary) plus structured
fields. Foundry IQ can parse it with ``outputParsing.kind = "json"`` and
``documentsPath = "$.results[*]"``.
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import re
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DATA_FILE = Path(os.environ.get("INVENTORY_FILE", Path(__file__).parent / "data" / "inventory.json"))

# Italian -> English terms so technicians can search in Italian against English specs.
SYNONYMS: dict[str, list[str]] = {
    "ricambio": [], "ricambi": [], "pezzo": [], "pezzi": [], "componente": [], "componenti": [],
    "filtro": ["filter"], "filtri": ["filter"], "membrana": ["membrane"],
    "sensore": ["sensor"], "sensori": ["sensor"], "sonda": ["probe"],
    "motore": ["motor"], "motori": ["motor"], "attuatore": ["actuator"],
    "rullo": ["roller"], "rulli": ["roller"], "ugello": ["nozzle"], "ugelli": ["nozzle"],
    "testina": ["head"], "testine": ["head"], "stampa": ["print"], "cartuccia": ["cartridge"],
    "inchiostro": ["ink"], "lama": ["blade"], "taglio": ["cutting"], "cucitura": ["sewing", "stitch"],
    "valvola": ["valve"], "pressione": ["pressure"], "connettore": ["connector"],
    "tubo": ["line"], "fluido": ["fluid"], "serbatoio": ["tank", "reservoir", "vessel"],
    "bobina": ["coil"], "serpentina": ["coil"], "riscaldatore": ["heater"], "riscaldamento": ["heating"],
    "temperatura": ["temperature"], "raffreddamento": ["cooling"], "alimentatore": ["adapter", "power"],
    "alimentazione": ["power", "supply", "feed"], "pannello": ["panel"], "controllo": ["control"],
    "sicurezza": ["safety"], "interblocco": ["interlock"], "interruttore": ["switch"], "rele": ["relay"],
    "braccio": ["arm"], "giunto": ["joint"], "telecamera": ["camera"], "laser": ["laser"],
    "allineamento": ["alignment"], "tensione": ["tension"], "vassoio": ["tray"], "molla": ["spring"],
    "estrusore": ["extruder"], "estrusione": ["extrusion"], "messa": ["grounding"], "terra": ["grounding"],
    "magazzino": [], "scorta": [], "disponibile": [], "disponibilita": [], "per": [], "della": [],
    "del": [], "di": [], "la": [], "il": [], "lo": [], "le": [], "i": [], "gli": [], "un": [], "una": [],
}
STOPWORDS = {"the", "a", "an", "for", "of", "and", "or", "part", "parts", "spare", "with", "in", "on"}


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9\-/ ]+", " ", text.lower())


def _terms(query: str) -> list[str]:
    out: list[str] = []
    for tok in _norm(query).split():
        if tok in SYNONYMS:
            out.extend(SYNONYMS[tok])
        elif tok not in STOPWORDS:
            out.append(tok)
    return list(dict.fromkeys(out))


SOURCE = os.environ.get("INVENTORY_SOURCE", "json").strip().lower()
CACHE_SECONDS = int(os.environ.get("INVENTORY_CACHE_SECONDS", "300"))
FALLBACK_TO_JSON = os.environ.get("INVENTORY_FALLBACK_TO_JSON", "false").strip().lower() in {"1", "true", "yes"}
_RETRY_SECONDS = 60

_lock = threading.Lock()
_state: dict[str, Any] = {"data": None, "expires": 0.0, "source": None, "error": None}


def _load_json() -> dict[str, Any]:
    with DATA_FILE.open(encoding="utf-8") as f:
        return json.load(f)


def _fetch() -> tuple[dict[str, Any], str]:
    if SOURCE == "sql":
        import sql_source

        return sql_source.load(), "sql"
    return _load_json(), "json"


def _load() -> dict[str, Any]:
    """Return the catalog; SQL data is cached for INVENTORY_CACHE_SECONDS, JSON for the process lifetime."""
    now = time.monotonic()
    if _state["data"] is not None and now < _state["expires"]:
        return _state["data"]
    with _lock:
        if _state["data"] is not None and now < _state["expires"]:
            return _state["data"]
        try:
            data, source = _fetch()
            ttl = CACHE_SECONDS if source == "sql" else float("inf")
            _state["error"] = None
        except Exception as exc:  # keep serving when the database is unreachable
            logger.exception("Inventory load from %s failed", SOURCE)
            _state["error"] = f"{type(exc).__name__}: {exc}"
            if _state["data"] is not None:
                _state["expires"] = now + _RETRY_SECONDS
                return _state["data"]
            if not FALLBACK_TO_JSON:
                raise
            data, source, ttl = _load_json(), "json-fallback", _RETRY_SECONDS
        data["_vocabulary"] = _build_vocabulary(data["parts"])
        _state.update(data=data, source=source, expires=now + ttl)
        return data


def status() -> dict[str, Any]:
    """Data source health used by /healthz."""
    try:
        data = _load()
    except Exception:
        return {"source": SOURCE, "parts": 0, "error": _state["error"]}
    out: dict[str, Any] = {"source": _state["source"], "parts": len(data["parts"])}
    if _state["error"]:
        out["error"] = _state["error"]
    return out


def _parts() -> list[dict[str, Any]]:
    return _load()["parts"]


def _warehouses() -> dict[str, dict[str, Any]]:
    return {w["code"]: w for w in _load()["warehouses"]}


def _index() -> dict[str, dict[str, Any]]:
    return {p["part_number"].upper(): p for p in _parts()}


def _availability(part: dict[str, Any], warehouse: str | None = None) -> dict[str, Any]:
    rows = [s for s in part["stock"] if not warehouse or s["warehouse"] == warehouse]
    on_hand = sum(s["on_hand"] for s in rows)
    reserved = sum(s["reserved"] for s in rows)
    return {"on_hand": on_hand, "reserved": reserved, "available": on_hand - reserved}


def _status(part: dict[str, Any]) -> str:
    available = _availability(part)["available"]
    if available <= 0:
        return "out of stock"
    if available < part["reorder_point"]:
        return "below reorder point"
    return "in stock"


def _summary(part: dict[str, Any]) -> str:
    wh = _warehouses()
    av = _availability(part)
    locations = "; ".join(
        f"{wh[s['warehouse']]['name']} ({s['warehouse']}) bin {s['bin']}: {s['on_hand'] - s['reserved']} available"
        f" ({s['on_hand']} on hand, {s['reserved']} reserved)"
        for s in part["stock"]
    ) or "no stock locations"
    lines = [
        f"{part['name']} - part number {part['part_number']}, machine: {part['machine']}, category: {part['category']}.",
        part["description"],
        f"Stock status: {_status(part)}. Total available {av['available']} (on hand {av['on_hand']}, reserved {av['reserved']}), "
        f"reorder point {part['reorder_point']}. Locations: {locations}.",
        f"Supplier: {part['supplier']}, lead time {part['lead_time_days']} days, unit cost EUR {part['unit_cost_eur']:.2f}, "
        f"criticality {part['criticality']}.",
    ]
    if part["open_orders"]:
        lines.append("Open purchase orders: " + "; ".join(
            f"{o['po']} qty {o['quantity']} ETA {o['eta']} ({o['status']})" for o in part["open_orders"]) + ".")
    if part["alternates"]:
        lines.append("Alternate parts: " + "; ".join(f"{a['part_number']} ({a['note']})" for a in part["alternates"]) + ".")
    if part.get("maintenance_note"):
        lines.append(f"Maintenance note: {part['maintenance_note']}")
    return "\n".join(lines)


def _result(part: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "id": part["part_number"],
        "title": f"{part['part_number']} - {part['name']} ({part['machine']})",
        "content": _summary(part),
        "part_number": part["part_number"],
        "name": part["name"],
        "machine": part["machine"],
        "category": part["category"],
        "standard": part["standard"],
        "stock_status": _status(part),
        **_availability(part),
        "reorder_point": part["reorder_point"],
        "lead_time_days": part["lead_time_days"],
        "supplier": part["supplier"],
        "unit_cost_eur": part["unit_cost_eur"],
        "criticality": part["criticality"],
        "stock": part["stock"],
        "open_orders": part["open_orders"],
        "alternates": part["alternates"],
        **extra,
    }


def _envelope(results: list[dict[str, Any]], message: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"count": len(results), "results": results}
    if message:
        out["message"] = message
    return out


def _match_machine(part: dict[str, Any], machine: str | None) -> bool:
    if not machine:
        return True
    wanted = _norm(machine).split()
    target = _norm(part["machine"] + " " + part["part_number"].split("-")[0])
    return all(w in target for w in wanted)


def _build_vocabulary(parts: list[dict[str, Any]]) -> list[str]:
    words: set[str] = set()
    for p in parts:
        words.update(_norm(f"{p['name']} {p['category']} {p['machine']}").split())
    return sorted(words)


def _vocabulary() -> list[str]:
    return _load()["_vocabulary"]


def _score(part: dict[str, Any], query: str, terms: list[str]) -> float:
    pn = part["part_number"].lower()
    q = _norm(query).strip()
    score = 0.0
    if q and q.replace(" ", "") == pn:
        score += 100
    elif len(q) >= 3 and q.replace(" ", "") in pn:
        score += 40
    fields = [
        (_norm(part["name"]), 10), (_norm(part["category"]), 6), (_norm(part["machine"]), 4),
        (pn, 8), (_norm(part["description"] + " " + (part.get("maintenance_note") or "") + " " + (part["standard"] or "")), 2),
    ]
    vocab = _vocabulary()
    matched = 0
    for term in terms:
        candidates = [term] + [c for c in difflib.get_close_matches(term, vocab, n=2, cutoff=0.8) if c != term]
        best = 0.0
        for i, cand in enumerate(candidates):
            weight = 1.0 if i == 0 else 0.6
            for text, w in fields:
                if re.search(rf"\b{re.escape(cand)}", text):
                    best = max(best, w * weight)
        score += best
        matched += best > 0
    if terms:
        # Prefer parts matching more of the query terms.
        score *= 0.5 + 0.5 * matched / len(terms)
    return score


def search_parts(query: str, machine: str | None = None, category: str | None = None,
                 warehouse: str | None = None, in_stock_only: bool = False, top: int = 10) -> dict[str, Any]:
    terms = _terms(query)
    top = max(1, min(top, 50))
    scored = []
    for p in _parts():
        if not _match_machine(p, machine):
            continue
        if category and _norm(category).strip() != p["category"]:
            continue
        if warehouse and not any(s["warehouse"] == warehouse.upper() for s in p["stock"]):
            continue
        if in_stock_only and _availability(p, warehouse.upper() if warehouse else None)["available"] <= 0:
            continue
        s = _score(p, query, terms) if (terms or query.strip()) else 1.0
        if s > 0:
            scored.append((s, p))
    scored.sort(key=lambda x: (-x[0], x[1]["part_number"]))
    results = [_result(p, relevance=round(s, 2)) for s, p in scored[:top]]
    return _envelope(results, None if results else f"No parts match '{query}'. Try a broader term, a part number or a machine name.")


def search_parts_multi(queries: list[str], machine: str | None = None, category: str | None = None,
                       warehouse: str | None = None, in_stock_only: bool = False, top: int = 10) -> dict[str, Any]:
    """Run several searches (e.g. the sub-queries planned by Foundry IQ) and merge them by best relevance."""
    queries = [q for q in (q.strip() for q in queries) if q]
    if len(queries) <= 1:
        return search_parts(queries[0] if queries else "", machine, category, warehouse, in_stock_only, top)
    best: dict[str, dict[str, Any]] = {}
    for q in queries:
        for r in search_parts(q, machine, category, warehouse, in_stock_only, top)["results"]:
            if r["id"] not in best or r["relevance"] > best[r["id"]]["relevance"]:
                best[r["id"]] = r
    results = sorted(best.values(), key=lambda r: (-r["relevance"], r["id"]))[: max(1, min(top, 50))]
    return _envelope(results, None if results else f"No parts match {queries}. Try a broader term, a part number or a machine name.")


def get_part(part_number: str) -> dict[str, Any]:
    part = _index().get(part_number.strip().upper())
    if not part:
        close = difflib.get_close_matches(part_number.strip().upper(), list(_index()), n=3, cutoff=0.6)
        return _envelope([], f"Part {part_number} not found." + (f" Did you mean: {', '.join(close)}?" if close else ""))
    return _envelope([_result(part)])


def check_stock(part_numbers: list[str], warehouse: str | None = None) -> dict[str, Any]:
    wh = _warehouses()
    code = warehouse.upper() if warehouse else None
    if code and code not in wh:
        return _envelope([], f"Unknown warehouse {warehouse}. Valid codes: {', '.join(wh)}.")
    results, missing = [], []
    idx = _index()
    for pn in part_numbers:
        part = idx.get(pn.strip().upper())
        if not part:
            missing.append(pn)
            continue
        av = _availability(part, code)
        scope = f"in {wh[code]['name']} ({code})" if code else "across all warehouses"
        content = (f"{part['part_number']} {part['name']}: {av['available']} available {scope} "
                   f"(on hand {av['on_hand']}, reserved {av['reserved']}). Overall status: {_status(part)}.")
        if av["available"] <= 0 and part["open_orders"]:
            o = part["open_orders"][0]
            content += f" Next delivery: {o['po']} qty {o['quantity']} ETA {o['eta']} ({o['status']})."
        elif av["available"] <= 0:
            content += f" No open orders; supplier lead time {part['lead_time_days']} days."
        results.append(_result(part, content=content, scope_warehouse=code, scope_availability=av))
    return _envelope(results, f"Unknown part numbers: {', '.join(missing)}." if missing else None)


def list_machine_parts(machine: str) -> dict[str, Any]:
    results = [_result(p) for p in _parts() if _match_machine(p, machine)]
    machines = sorted({p["machine"] for p in _parts()})
    return _envelope(results, None if results else f"No machine matches '{machine}'. Known machines: {'; '.join(machines)}.")


def find_alternatives(part_number: str) -> dict[str, Any]:
    idx = _index()
    part = idx.get(part_number.strip().upper())
    if not part:
        return get_part(part_number)
    results = [_result(idx[a["part_number"]], substitution_note=a["note"], substitute_for=part["part_number"])
               for a in part["alternates"] if a["part_number"] in idx]
    return _envelope(results, None if results else
                     f"No approved alternates for {part['part_number']}. Check open orders or supplier lead time ({part['lead_time_days']} days).")


def low_stock_report(warehouse: str | None = None, criticality: str | None = None) -> dict[str, Any]:
    code = warehouse.upper() if warehouse else None
    results = []
    for p in _parts():
        if criticality and p["criticality"] != criticality.lower():
            continue
        if code and not any(s["warehouse"] == code for s in p["stock"]):
            continue
        if _status(p) != "in stock":
            results.append(_result(p))
    order = {"high": 0, "medium": 1, "low": 2}
    results.sort(key=lambda r: (order[r["criticality"]], r["available"] - r["reorder_point"]))
    return _envelope(results, None if results else "All parts are above their reorder point.")


def list_warehouses() -> dict[str, Any]:
    results = []
    for code, w in _warehouses().items():
        rows = [(p, s) for p in _parts() for s in p["stock"] if s["warehouse"] == code]
        units = sum(s["on_hand"] for _, s in rows)
        empty = sorted(p["part_number"] for p, s in rows if s["on_hand"] - s["reserved"] <= 0)
        content = (f"{w['name']} ({code}), type {w['type']}, site {w['site']}: {len(rows)} part numbers stocked, "
                   f"{units} units on hand." + (f" Locations with no available units: {', '.join(empty)}." if empty else ""))
        results.append({"id": code, "title": f"{code} - {w['name']}", "content": content, **w,
                        "part_numbers": len(rows), "units_on_hand": units})
    return _envelope(results)

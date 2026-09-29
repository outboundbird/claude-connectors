import asyncio
import csv
import logging
import os
import re
import ssl
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import httpx
import truststore
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

BASE = "https://proteomecentral.proteomexchange.org/api/proxi/v0.1"
MAX_PAGE = 500

mcp = MCPServer(
    "proteomexchange",
    instructions="""MCP server for ProteomeCentral (ProteomeXchange), which indexes proteomics datasets from
all member repositories: PRIDE, MassIVE, jPOST, iProX, PeptideAtlas, PanoramaPublic.

TOOL SELECTION GUIDE:
- find_datasets: main search. Diseases have no structured field here, so pass disease names and synonyms
  as search_terms (e.g. ["ulcerative colitis", "Crohn", "inflammatory bowel disease"]); results are unioned.
  Fetches every match, writes the full list to a CSV at the user's chosen output_path (ask the user for
  the location if they haven't given one), and returns a summary plus the CSV path. Always give the user
  the CSV path.
  Structured filters (species, instrument, repository, keywords, year, sdrf) need exact facet values.
- list_filter_values: valid species/instrument/repository/keyword/year/SDRF values with counts,
  optionally scoped by a search term. Species use names like "Homo sapiens", not "human".
- get_dataset: full record for a PXD/MSV/JPST/IPX/PASS accession (description, contacts, publications,
  modifications, SDRF status, FTP links).
- list_dataset_files: file URIs for a dataset, grouped by type (raw, search output, result, etc.).
- search_libraries: spectral libraries.

Use this server for cross-repository coverage; the separate `pride` server has richer disease/tissue tags for
PRIDE-hosted datasets.""",
)

logging.getLogger("httpx").setLevel(logging.WARNING)

# OS trust store so corporate TLS-inspection root CAs are honoured.
_ssl_ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
_client = httpx.AsyncClient(base_url=BASE, timeout=90, verify=_ssl_ctx, headers={"Accept": "application/json"})

_COMPACT_FIELDS = [
    "accession", "title", "repository", "species", "sdrf", "files", "instrument",
    "publications", "labHead", "announceDate", "keywords",
]
_CSV_FIELDS = [
    "accession", "title", "repository", "species", "instrument", "announceDate", "publications",
    "labHead", "keywords", "rawFiles", "totalFiles", "sdrf", "matchedTerms", "url",
]
_DATASET_URL = "https://proteomecentral.proteomexchange.org/cgi/GetDataset?ID="
_TAG = re.compile(r"<[^>]+>")


def _truncate(text: str | None, n: int) -> str | None:
    if not text or len(text) <= n:
        return text
    return text[:n].rstrip() + "…"


async def _get(path: str, **params: Any) -> Any:
    params = {k: v for k, v in params.items() if v is not None and v != ""}
    r = await _client.get(path, params=params)
    if r.status_code == 404:
        try:
            detail = r.json().get("description")
        except ValueError:
            detail = None
        return {"notFound": True, "path": path, "detail": detail}
    r.raise_for_status()
    return r.json()


def _page_size(n: int) -> int:
    return max(1, min(n, MAX_PAGE))


def _row(values: list) -> dict:
    row = dict(zip(_COMPACT_FIELDS, values))
    if row.get("publications"):
        row["publications"] = _TAG.sub("", row["publications"])
    if row.get("keywords") in ("None", ""):
        row["keywords"] = None
    return row


async def _search_all(term: str | None, limit: int, **filters: Any) -> dict:
    rows: list[dict] = []
    page, total = 1, 0
    while len(rows) < limit:
        data = await _get(
            "/datasets", resultType="compact", search=term, pageNumber=page, pageSize=MAX_PAGE, **filters
        )
        total = data["result_set"]["n_available_rows"]
        batch = data.get("datasets", [])
        rows += [_row(d) for d in batch]
        if not batch or len(rows) >= total:
            break
        page += 1
    return {"total": total, "rows": rows[:limit]}


def _slug(terms: list[str | None]) -> str:
    text = "_".join(t for t in terms if t) or "all"
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")[:60]


def _resolve_csv_path(output_path: str | None, terms: list[str | None]) -> Path | None:
    target = output_path or os.environ.get("PX_EXPORT_DIR")
    if not target:
        return None
    path = Path(target).expanduser()
    # The server's cwd is its own repo folder; relative paths would silently land there.
    if not path.is_absolute():
        raise ToolError(f"output_path must be an absolute path, got {target!r}. Ask the user for a full path.")
    if path.suffix.lower() != ".csv":
        path = path / f"px_{_slug(terms)}_{datetime.now():%Y%m%d-%H%M%S}.csv"
    return path


def _write_csv(rows: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig so Excel detects UTF-8 (accented author names, en dashes in titles).
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            raw, _, total = (r.get("files") or "").partition("/")
            writer.writerow({**r, "rawFiles": raw, "totalFiles": total, "matchedTerms": "; ".join(r["matchedTerms"])})
    return path


def _year(r: dict) -> str:
    return (r.get("announceDate") or "")[:4] or "unknown"


@mcp.tool()
async def find_datasets(
    search_terms: list[str] | None = None,
    species: str | None = None,
    instrument: str | None = None,
    repository: Literal["PRIDE", "MassIVE", "jPOST", "iProX", "PeptideAtlas", "PanoramaPublic"] | None = None,
    keywords: str | None = None,
    year: int | None = None,
    sdrf: str | None = None,
    modification: str | None = None,
    contact: str | None = None,
    max_per_term: int = 2000,
    preview_rows: int = 10,
    output_path: str | None = None,
) -> dict:
    """Search ProteomeXchange datasets across all repositories, export the full list to CSV,
    and return a summary.

    search_terms: free-text terms (disease names, synonyms, tissues, genes...). Each term is searched
    separately and results are merged and de-duplicated, so list synonyms,
    e.g. ["ulcerative colitis", "Crohn", "inflammatory bowel disease"]. Each term is matched as a phrase,
    so keep terms short (a multi-word combo like "IBD colitis" usually matches nothing).
    Other filters must match facet values from list_filter_values exactly
    (e.g. species="Homo sapiens", instrument="Q Exactive HF", keywords="DIA").

    All pages are fetched (up to max_per_term per term). Every unique dataset is written to a CSV with
    columns accession, title, repository, species, instrument, announceDate, publications, labHead,
    keywords, rawFiles, totalFiles, sdrf, matchedTerms, url.

    output_path: where the user wants the CSV, as an absolute path to a folder (a timestamped file name
    is generated) or to a .csv file. Use the location the user gave; if they haven't said, ask them
    before calling. Falls back to $PX_EXPORT_DIR; if neither is set, no CSV is written and only the
    summary is returned. Tell the user the CSV path.
    """
    csv_path = _resolve_csv_path(output_path, search_terms or [None])
    filters = dict(
        species=species, instrument=instrument, repository=repository, keywords=keywords,
        year=year, sdrf=sdrf, modification=modification, contact=contact,
    )
    terms = search_terms or [None]
    results = await asyncio.gather(*(_search_all(t, max_per_term, **filters) for t in terms))

    merged: dict[str, dict] = {}
    for term, res in zip(terms, results):
        for row in res["rows"]:
            entry = merged.setdefault(
                row["accession"], {**row, "matchedTerms": [], "url": _DATASET_URL + row["accession"]}
            )
            if term:
                entry["matchedTerms"].append(term)
    rows = sorted(merged.values(), key=lambda r: r.get("announceDate") or "", reverse=True)
    if csv_path:
        _write_csv(rows, csv_path)

    species_counts = Counter(s.strip() for r in rows for s in (r.get("species") or "unknown").split(","))
    return {
        "csvPath": str(csv_path) if csv_path else None,
        **(
            {}
            if csv_path
            else {"csvNotSaved": "No output_path given. Ask the user where to save the CSV, then call again with output_path."}
        ),
        "uniqueDatasets": len(rows),
        "matchesPerTerm": {t or "(all)": r["total"] for t, r in zip(terms, results)},
        "truncatedTerms": [t for t, r in zip(terms, results) if r["total"] > len(r["rows"])],
        "byRepository": dict(Counter(r.get("repository") or "unknown" for r in rows).most_common()),
        "byYear": dict(sorted(Counter(_year(r) for r in rows).items(), reverse=True)),
        "topSpecies": dict(species_counts.most_common(8)),
        "topInstruments": dict(Counter(r.get("instrument") or "unknown" for r in rows).most_common(5)),
        "newest": [
            {
                "accession": r["accession"],
                "date": r.get("announceDate"),
                "repository": r.get("repository"),
                "species": r.get("species"),
                "title": _truncate(r.get("title"), 110),
            }
            for r in rows[: max(0, preview_rows)]
        ],
    }


@mcp.tool()
async def list_filter_values(
    search: str | None = None,
    species: str | None = None,
    field: Literal["species", "instrument", "repository", "keywords", "year", "sdrf", "files", "all"] = "all",
    top_n: int = 25,
) -> dict:
    """Valid filter values with dataset counts (facets), optionally scoped by a search term and species.

    Use to discover exact names for find_datasets, or to summarise a topic
    (e.g. search="Alzheimer" -> which species, instruments, repositories dominate).
    """
    data = await _get("/datasets", resultType="compact", pageSize=1, search=search, species=species)
    facets = data.get("facets", {})
    if field != "all":
        facets = {field: facets.get(field, [])}
    return {
        "totalDatasets": data["result_set"]["n_available_rows"],
        "facets": {k: {f["name"]: f["count"] for f in v[:top_n]} for k, v in facets.items() if v},
    }


def _terms(block: dict) -> dict:
    return {t.get("name"): t.get("value", True) for t in block.get("terms", [])}


@mcp.tool()
async def get_dataset(accession: str) -> dict:
    """Full metadata for one dataset (PXD..., MSV..., JPST..., IPX..., PASS...). File lists are omitted;
    use list_dataset_files for those."""
    d = await _get(f"/datasets/{accession}")
    if d.get("notFound") or d.get("status") == "ERROR":
        return d
    sdrf = d.get("sdrf_metadata") or {}
    return {
        "accession": accession,
        "title": d.get("title"),
        "description": d.get("description"),
        "hostingRepository": (d.get("datasetSummary") or {}).get("hostingRepository"),
        "announceDate": (d.get("datasetSummary") or {}).get("announceDate"),
        "species": [_terms(s) for s in d.get("species", [])],
        "instruments": [i.get("name") for i in d.get("instruments", [])],
        "keywords": [k.get("value") for k in d.get("keywords", [])],
        "modifications": [m.get("name") for m in d.get("modifications", [])],
        "publications": [_terms(p) for p in d.get("publications", [])],
        "contacts": [_terms(c) for c in d.get("contacts", [])],
        "links": {l.get("name"): l.get("value") for l in d.get("fullDatasetLinks", [])},
        "sdrf": {
            "bestSource": sdrf.get("sdrf_best_source"),
            "externalUrl": sdrf.get("external_sdrf_ui_url") or sdrf.get("external_sdrf_data_url"),
        },
        "fileCount": len(d.get("datasetFiles", [])),
    }


@mcp.tool()
async def list_dataset_files(accession: str, file_type: str | None = None, limit: int = 200) -> dict:
    """File URIs for a dataset grouped by type (e.g. "Associated raw file URI", "Search engine output file URI",
    "Result file URI"). file_type filters by a case-insensitive substring such as "raw" or "result"."""
    d = await _get(f"/datasets/{accession}")
    if d.get("notFound") or d.get("status") == "ERROR":
        return d
    groups: dict[str, list[str]] = {}
    for f in d.get("datasetFiles", []):
        name = f.get("name", "other")
        if file_type and file_type.lower() not in name.lower():
            continue
        groups.setdefault(name, []).append(f.get("value"))
    return {
        "counts": {k: len(v) for k, v in groups.items()},
        "files": {k: v[:limit] for k, v in groups.items()},
    }


@mcp.tool()
async def search_libraries(
    search: str | None = None,
    species: str | None = None,
    fragmentation_type: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """Search spectral libraries indexed by ProteomeCentral."""
    data = await _get(
        "/libraries",
        resultType="compact",
        search=search,
        species=species,
        fragmentation_type=fragmentation_type,
        pageNumber=page,
        pageSize=_page_size(page_size),
    )
    titles = (data.get("result_set") or {}).get("datasets_title_list") or (data.get("result_set") or {}).get(
        "libraries_title_list"
    )
    rows = data.get("libraries") or data.get("datasets") or []
    if titles:
        rows = [dict(zip(titles, r)) if isinstance(r, list) else r for r in rows]
    return {"total": (data.get("result_set") or {}).get("n_available_rows"), "results": rows}


if __name__ == "__main__":
    mcp.run()

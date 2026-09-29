import asyncio
import logging
import re
import ssl
from typing import Any, Literal

import httpx
import truststore
from mcp.server.mcpserver import MCPServer

BASE = "https://proteomecentral.proteomexchange.org/api/proxi/v0.1"
MAX_PAGE = 500

mcp = MCPServer(
    "proteomexchange",
    instructions="""MCP server for ProteomeCentral (ProteomeXchange), which indexes proteomics datasets from
all member repositories: PRIDE, MassIVE, jPOST, iProX, PeptideAtlas, PanoramaPublic.

TOOL SELECTION GUIDE:
- find_datasets: main search. Diseases have no structured field here, so pass disease names and synonyms
  as search_terms (e.g. ["ulcerative colitis", "Crohn", "inflammatory bowel disease"]); results are unioned.
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
_BRIEF_FIELDS = ["accession", "title", "repository", "species", "instrument", "announceDate", "matchedTerms"]
_TAG = re.compile(r"<[^>]+>")


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


async def _search(term: str | None, page: int, page_size: int, **filters: Any) -> dict:
    data = await _get(
        "/datasets",
        resultType="compact",
        search=term,
        pageNumber=page,
        pageSize=_page_size(page_size),
        **filters,
    )
    return {
        "total": data["result_set"]["n_available_rows"],
        "rows": [_row(d) for d in data.get("datasets", [])],
    }


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
    page: int = 1,
    page_size: int = 100,
    max_results: int = 50,
    offset: int = 0,
    detail: Literal["brief", "full"] = "brief",
) -> dict:
    """Search ProteomeXchange datasets across all repositories.

    search_terms: free-text terms (disease names, synonyms, tissues, genes...). Each term is searched
    separately and results are merged and de-duplicated, so list synonyms,
    e.g. ["ulcerative colitis", "Crohn", "inflammatory bowel disease"]. Each term is matched as a phrase,
    so keep terms short (a multi-word combo like "IBD colitis" usually matches nothing).
    Other filters must match facet values from list_filter_values exactly
    (e.g. species="Homo sapiens", instrument="Q Exactive HF", keywords="DIA").
    page/page_size page through the API per search term (page is 1-based).
    The merged, de-duplicated list is newest first; max_results/offset slice it to keep responses small
    (use offset to read further). detail="brief" returns accession, title, repository, species,
    instrument, date and matched terms; "full" adds publications, lab head, keywords, file and SDRF info.
    """
    filters = dict(
        species=species, instrument=instrument, repository=repository, keywords=keywords,
        year=year, sdrf=sdrf, modification=modification, contact=contact,
    )
    terms = search_terms or [None]
    results = await asyncio.gather(*(_search(t, page, page_size, **filters) for t in terms))

    merged: dict[str, dict] = {}
    for term, res in zip(terms, results):
        for row in res["rows"]:
            entry = merged.setdefault(row["accession"], {**row, "matchedTerms": []})
            if term:
                entry["matchedTerms"].append(term)
    rows = sorted(merged.values(), key=lambda r: r.get("announceDate") or "", reverse=True)
    window = rows[offset : offset + max(1, max_results)]
    if detail == "brief":
        window = [{k: r.get(k) for k in _BRIEF_FIELDS} for r in window]
        for r in window:
            if r["title"] and len(r["title"]) > 150:
                r["title"] = r["title"][:150].rstrip() + "…"
    return {
        "totalMatchesPerTerm": {t or "(all)": r["total"] for t, r in zip(terms, results)},
        "uniqueFound": len(rows),
        "offset": offset,
        "returned": len(window),
        "moreAvailable": offset + len(window) < len(rows),
        "results": window,
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

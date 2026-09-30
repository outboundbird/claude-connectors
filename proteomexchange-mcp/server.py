import asyncio
import logging
import re
import ssl
from collections import Counter
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
  Returns aggregated summary statistics plus a `rows` list with all unique datasets.
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
_pride = httpx.AsyncClient(
    base_url="https://www.ebi.ac.uk/pride/ws/archive/v3", timeout=60, verify=_ssl_ctx,
    headers={"Accept": "application/json"},
)

_COMPACT_FIELDS = [
    "accession", "title", "repository", "species", "sdrf", "files", "instrument",
    "publications", "labHead", "announceDate", "keywords",
]
_CSV_FIELDS = [
    "accession", "title", "repository", "species", "tissueType", "tissueSource", "instrument",
    "announceDate", "publications", "labHead", "keywords", "rawFiles", "totalFiles", "sdrf",
    "matchedTerms", "url",
]

# Sample-source categories inferred from free text when no curated annotation exists.
# Order is display order; each pattern is matched case-insensitively on word boundaries.
_TISSUE_PATTERNS = [
    # Labels follow PRIDE/BTO naming so curated and inferred values count together.
    ("Blood plasma", r"plasma(?!\s+cells?)"),
    ("Blood serum", r"sera|serum"),
    ("Blood", r"whole blood|peripheral blood|blood samples?|PBMCs?|erythrocytes?|platelets?"),
    ("Feces", r"stools?|feces|faeces|fecal|faecal"),
    ("Colon", r"colon|colonic|colorectal mucosa|rectum|rectal|sigmoid"),
    ("Small intestine", r"ileum|ileal|jejun\w*|duoden\w*|small intestine|terminal ileum"),
    ("Intestine", r"intestin\w*|gut mucosa|gut tissue"),
    ("Biopsy", r"biops\w*"),
    ("Lymph node", r"lymph|lymph nodes?|mesenteric"),
    ("Liver", r"liver|hepatocytes?|hepatic tissue"),
    ("Urine", r"urine|urinary"),
    ("Saliva", r"saliva|salivary"),
    ("Cerebrospinal fluid", r"cerebrospinal fluid|CSF"),
    ("Synovial fluid", r"synovial fluid"),
    ("Skin", r"skin|epiderm\w*|dermis"),
    ("Brain", r"brain|cortex|hippocamp\w*"),
    ("Lung", r"lungs?|pulmonary|bronch\w*"),
    ("Kidney", r"kidneys?|renal"),
    ("Tumor tissue", r"tumou?rs?|carcinoma tissue"),
    ("Organoid", r"organoids?|colonoids?|enteroids?"),
    ("Cell line", r"cell lines?|Caco-?2|HT-?29|DLD-?1|HEK-?293\w*|HeLa|THP-?1|Jurkat"),
    ("Primary cells", r"macrophages?|monocytes?|T cells?|B cells?|neutrophils?|epithelial cells?|fibroblasts?"),
    ("Bacterial culture", r"bacterial (?:cultures?|isolates?|strains?)|E\. ?coli (?:isolates?|strains?)"),
]
_TISSUE_RES = [(label, re.compile(rf"\b(?:{pat})\b", re.I)) for label, pat in _TISSUE_PATTERNS]
_TISSUE_CONCURRENCY = 8


def _infer_tissue(specific: list[str | None], background: str | None) -> list[str]:
    # Descriptions often name organs only as disease background, so use them only as a fallback.
    for text in (" ".join(t for t in specific if t), background or ""):
        found = [label for label, rx in _TISSUE_RES if rx.search(text)]
        if found:
            return found
    return []


async def _pride_tissue(accession: str) -> tuple[str, str] | None:
    r = await _pride.get(f"/projects/{accession}")
    if r.status_code != 200:
        return None
    p = r.json()
    curated = [o.get("name") for o in p.get("organismParts") or [] if o.get("name")]
    if curated:
        return "; ".join(curated), "PRIDE curated"
    inferred = _infer_tissue(
        [p.get("title"), " ".join(p.get("keywords") or []), p.get("sampleProcessingProtocol")],
        p.get("projectDescription"),
    )
    return ("; ".join(inferred), "inferred (PRIDE text)") if inferred else None


async def _proxi_tissue(accession: str) -> tuple[str, str] | None:
    d = await _get(f"/datasets/{accession}")
    if d.get("notFound") or d.get("status") == "ERROR":
        return None
    inferred = _infer_tissue(
        [d.get("title"), " ".join(k.get("value") or "" for k in d.get("keywords", []))], d.get("description")
    )
    return ("; ".join(inferred), "inferred (ProteomeXchange text)") if inferred else None


async def _add_tissue(rows: list[dict]) -> None:
    sem = asyncio.Semaphore(_TISSUE_CONCURRENCY)

    async def one(row: dict) -> None:
        async with sem:
            try:
                found = None
                if row.get("repository") == "PRIDE":
                    found = await _pride_tissue(row["accession"])
                if not found:
                    found = await _proxi_tissue(row["accession"])
            except httpx.HTTPError:
                row["tissueType"], row["tissueSource"] = "", "lookup failed"
                return
        row["tissueType"], row["tissueSource"] = found or ("", "not found")

    await asyncio.gather(*(one(r) for r in rows))
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
    include_tissue: bool = True,
) -> dict:
    """Search ProteomeXchange datasets across all repositories and return a summary plus raw rows.

    search_terms: free-text terms (disease names, synonyms, tissues, genes...). Each term is searched
    separately and results are merged and de-duplicated, so list synonyms,
    e.g. ["ulcerative colitis", "Crohn", "inflammatory bowel disease"]. Each term is matched as a phrase,
    so keep terms short (a multi-word combo like "IBD colitis" usually matches nothing).
    Other filters must match facet values from list_filter_values exactly
    (e.g. species="Homo sapiens", instrument="Q Exactive HF", keywords="DIA").

    All pages are fetched (up to max_per_term per term). Returns aggregated summary statistics
    plus a `rows` list with all unique datasets (accession, title, repository, species, tissueType,
    instrument, announceDate, publications, keywords, matchedTerms, url).

    tissueType is the biological sample source (e.g. blood plasma, stool, colon, biopsy, cell line).
    For PRIDE-hosted datasets it is PRIDE's curated organism-part annotation; otherwise inferred
    from title/keywords. Tissue lookup costs one extra request per dataset; set include_tissue=False
    for very large result sets.
    """
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
    if include_tissue:
        await _add_tissue(rows)

    species_counts = Counter(s.strip() for r in rows for s in (r.get("species") or "unknown").split(","))
    tissue_counts = Counter(
        t.strip().capitalize() for r in rows for t in (r.get("tissueType") or "unknown").split(";") if t.strip()
    )
    return {
        "uniqueDatasets": len(rows),
        "matchesPerTerm": {t or "(all)": r["total"] for t, r in zip(terms, results)},
        "truncatedTerms": [t for t, r in zip(terms, results) if r["total"] > len(r["rows"])],
        "byRepository": dict(Counter(r.get("repository") or "unknown" for r in rows).most_common()),
        "byYear": dict(sorted(Counter(_year(r) for r in rows).items(), reverse=True)),
        "topSpecies": dict(species_counts.most_common(8)),
        **(
            {
                "topTissueTypes": dict(tissue_counts.most_common(10)),
                "tissueSources": dict(Counter(r["tissueSource"] for r in rows).most_common()),
            }
            if include_tissue
            else {}
        ),
        "topInstruments": dict(Counter(r.get("instrument") or "unknown" for r in rows).most_common(5)),
        "newest": [
            {
                "accession": r["accession"],
                "date": r.get("announceDate"),
                "repository": r.get("repository"),
                "species": r.get("species"),
                "tissue": r.get("tissueType"),
                "title": _truncate(r.get("title"), 110),
            }
            for r in rows[: max(0, preview_rows)]
        ],
        "rows": rows,
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

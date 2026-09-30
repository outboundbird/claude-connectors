import logging
import ssl
from typing import Any, Literal

import httpx
import truststore
from mcp.server.mcpserver import MCPServer

BASE = "https://www.ebi.ac.uk/pride/ws/archive/v3"
MAX_PAGE = 100

mcp = MCPServer(
    "pride",
    instructions="""MCP server for the EBI PRIDE Archive (proteomics / mass-spec datasets, PXD accessions).

TOOL SELECTION GUIDE:
- list_filter_values: call FIRST when filtering by disease/organism/tissue/etc. PRIDE filters need exact
  vocabulary (e.g. "Homo sapiens (human)", "Breast cancer"); this returns valid values with dataset counts.
- find_datasets: main search by keyword plus structured criteria. Returns compact summaries.
- autocomplete: quick term suggestions for a partial keyword.
- get_project: full metadata for one PXD/PAD accession (protocols, references, sample attributes).
- list_project_files / count_files_by_type / get_sdrf / get_file_checksums / get_file: file-level info and FTP URLs.
- similar_projects / reanalyses: related datasets and publications reusing a dataset.
- search_proteins / get_protein: which PRIDE projects report a UniProt accession.
- get_stats / dataset_status: archive statistics and public/private status.

TYPICAL WORKFLOW: list_filter_values(keyword=...) -> find_datasets(disease=..., organism=...) -> get_project -> list_project_files.""",
)

logging.getLogger("httpx").setLevel(logging.WARNING)

# OS trust store so corporate TLS-inspection root CAs are honoured.
_ssl_ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
_client = httpx.AsyncClient(
    base_url=BASE, timeout=60, verify=_ssl_ctx, headers={"Accept": "application/json, text/plain;q=0.9, */*;q=0.5"}
)


async def _request(path: str, **params: Any) -> httpx.Response:
    params = {k: v for k, v in params.items() if v is not None and v != ""}
    return await _client.get(path, params=params)


async def _get(path: str, **params: Any) -> Any:
    r = await _request(path, **params)
    if r.status_code == 404:
        return {"notFound": True, "path": path}
    r.raise_for_status()
    try:
        return r.json()
    except ValueError:
        return r.text


def _page_size(n: int) -> int:
    return max(1, min(n, MAX_PAGE))


def _build_filter(**fields: str | list[str] | None) -> str | None:
    parts = []
    for field, value in fields.items():
        values = [value] if isinstance(value, str) else (value or [])
        parts += [f"{field}=={v}" for v in values if v]
    return ",".join(parts) or None


def _truncate(text: str | None, n: int) -> str | None:
    if not text or len(text) <= n:
        return text
    return text[:n].rstrip() + "…"


def _compact_project(p: dict) -> dict:
    return {
        "accession": p.get("accession"),
        "title": p.get("title"),
        "description": _truncate(p.get("projectDescription"), 300),
        "organisms": p.get("organisms"),
        "organismsPart": p.get("organismsPart"),
        "diseases": p.get("diseases"),
        "instruments": p.get("instruments"),
        "experimentTypes": p.get("experimentTypes"),
        "quantificationMethods": p.get("quantificationMethods"),
        "submissionType": p.get("submissionType"),
        "publicationDate": p.get("publicationDate"),
        "downloadCount": p.get("downloadCount"),
        "numberOfSamples": p.get("numberOfSamples"),
    }


def _compact_file(f: dict) -> dict:
    return {
        "fileAccession": f.get("accession"),
        "fileName": f.get("fileName"),
        "category": (f.get("fileCategory") or {}).get("value"),
        "sizeBytes": f.get("fileSizeBytes"),
        "urls": {loc.get("name"): loc.get("value") for loc in f.get("publicFileLocations") or []},
    }


@mcp.tool()
async def find_datasets(
    keyword: str = "",
    organism: list[str] | None = None,
    disease: list[str] | None = None,
    tissue: list[str] | None = None,
    instrument: list[str] | None = None,
    experiment_type: list[str] | None = None,
    quantification_method: list[str] | None = None,
    software: list[str] | None = None,
    project_keyword: list[str] | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    sort_by: Literal["submissionDate", "publicationDate", "downloadCount"] = "submissionDate",
    sort_direction: Literal["DESC", "ASC"] = "DESC",
    page: int = 0,
    page_size: int = 25,
) -> dict:
    """Search PRIDE datasets by free-text keyword and structured criteria.

    Structured values must match PRIDE vocabulary exactly; get them from list_filter_values first
    (e.g. organism="Homo sapiens (human)", disease="Breast cancer", tissue="Liver",
    experiment_type="Shotgun proteomics", quantification_method="TMT", instrument="Q Exactive").
    year_from/year_to filter on publication year within the returned page.
    """
    flt = _build_filter(
        organisms=organism,
        diseases=disease,
        organismsPart=tissue,
        instruments=instrument,
        experimentTypes=experiment_type,
        quantificationMethods=quantification_method,
        softwares=software,
        keywords=project_keyword,
    )
    r = await _request(
        "/search/projects",
        keyword=keyword,
        filter=flt,
        page=page,
        pageSize=_page_size(page_size),
        sortFields=sort_by,
        sortDirection=sort_direction,
    )
    r.raise_for_status()
    projects = [_compact_project(p) for p in r.json()]
    if year_from or year_to:
        def in_range(p: dict) -> bool:
            year = int((p.get("publicationDate") or "0")[:4] or 0)
            return (not year_from or year >= year_from) and (not year_to or year <= year_to)
        projects = [p for p in projects if in_range(p)]
    total = r.headers.get("total_records")
    return {
        "filter": flt,
        "totalMatches": int(total) if total else None,
        "page": page,
        "returned": len(projects),
        "results": projects,
    }


@mcp.tool()
async def list_filter_values(
    keyword: str = "",
    field: Literal[
        "organisms", "diseases", "organismsPart", "instruments", "experimentTypes",
        "quantificationMethods", "softwares", "keywords", "projectTags", "submissionType",
        "otherOmicsLinks", "publicationDate", "all",
    ] = "all",
    organism: list[str] | None = None,
    disease: list[str] | None = None,
    tissue: list[str] | None = None,
    top_n: int = 25,
) -> dict:
    """Get valid filter values with dataset counts (facets), optionally scoped by keyword or existing filters.

    Use this to discover exact names for find_datasets, and to answer questions like
    "which diseases have the most human liver datasets?".
    """
    flt = _build_filter(organisms=organism, diseases=disease, organismsPart=tissue)
    data = await _get("/facet/projects", keyword=keyword, filter=flt, facetPageSize=_page_size(top_n))
    if field != "all":
        data = {field: data.get(field, {})}
    return {
        k: dict(sorted(v.items(), key=lambda kv: -kv[1])[:top_n])
        for k, v in data.items()
        if isinstance(v, dict) and v
    }


@mcp.tool()
async def autocomplete(keyword: str) -> list:
    """Suggest dataset titles/terms starting with a partial keyword."""
    return await _get("/search/autocomplete", keyword=keyword)


@mcp.tool()
async def get_project(accession: str) -> dict:
    """Full metadata for a PRIDE project (e.g. PXD000001), including protocols, references and sample attributes."""
    return await _get(f"/projects/{accession}")


@mcp.tool()
async def list_project_files(
    accession: str, filename_filter: str = "", page: int = 0, page_size: int = 50
) -> dict:
    """List files in a project with category (RAW, PEAK, RESULT, SEARCH, ...), size and FTP/Aspera URLs."""
    total = await _get(f"/projects/{accession}/files/count")
    files = await _get(
        f"/projects/{accession}/files",
        filenameFilter=filename_filter,
        page=page,
        pageSize=_page_size(page_size),
    )
    return {"totalFiles": total, "page": page, "files": [_compact_file(f) for f in files]}


@mcp.tool()
async def count_files_by_type(accession: str) -> dict:
    """Count a project's files by category (RAW, PEAK, RESULT, SEARCH, EXPERIMENTAL DESIGN, OTHER)."""
    return await _get(f"/files/getCountOfFilesByType/{accession}")


@mcp.tool()
async def get_sdrf(accession: str) -> list:
    """SDRF (sample and data relationship format) files for a project: sample-level metadata."""
    data = await _get(f"/files/sdrf/{accession}")
    return [_compact_file(f) if isinstance(f, dict) else f for f in data]


@mcp.tool()
async def get_file_checksums(accession: str) -> Any:
    """MD5 checksums for all files in a project."""
    return await _get(f"/files/checksum/{accession}")


@mcp.tool()
async def get_file(file_accession: str) -> dict:
    """Details for a single file by its file accession (from list_project_files)."""
    return _compact_file(await _get(f"/files/{file_accession}"))


@mcp.tool()
async def similar_projects(accession: str, page: int = 0, page_size: int = 10) -> list:
    """Projects with similar metadata to the given project."""
    data = await _get(f"/projects/{accession}/similarProjects", page=page, pageSize=_page_size(page_size))
    return [_compact_project(p) for p in data]


@mcp.tool()
async def reanalyses(accession: str) -> Any:
    """Publications/datasets that reanalyse the given project."""
    return await _get(f"/projects/reanalysis/{accession}")


@mcp.tool()
async def search_proteins(accession: str) -> Any:
    """Find PRIDE projects reporting a protein, by UniProt accession (e.g. P04637)."""
    return await _get("/proteins/search", accession=accession)


@mcp.tool()
async def get_protein(accession: str) -> Any:
    """Protein record by accession, including the list of PRIDE projects it appears in."""
    return await _get(f"/proteins/{accession}")


@mcp.tool()
async def get_stats(name: str = "submissions-monthly") -> Any:
    """Archive statistics. Use name="submissions-monthly" or "submitted-data", or a named stat
    (e.g. "SUBMISSIONS_PER_YEAR", "SUBMISSIONS_PER_INSTRUMENTS", "SUBMISSIONS_PER_DISEASES")."""
    return await _get(f"/stats/{name}")


@mcp.tool()
async def dataset_status(accession: str) -> Any:
    """Whether a dataset accession is PUBLIC or PRIVATE."""
    return await _get(f"/status/{accession}")


if __name__ == "__main__":
    mcp.run()

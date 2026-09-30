import logging
import ssl
from typing import Any, Literal

import httpx
import truststore
from mcp.server.mcpserver import MCPServer

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

mcp = MCPServer(
    "geo",
    instructions="""MCP server for NCBI GEO (Gene Expression Omnibus) via E-utilities.

GEO hosts gene expression and genomics datasets (RNA-seq, microarray, ChIP-seq, etc.).
For proteomics datasets, prefer the PRIDE or ProteomeXchange connectors instead.

TOOL SELECTION GUIDE:
- search_geo: Primary search by keyword + optional organism filter. Returns GSE accessions,
  titles, summaries, sample counts, organisms, platforms, and linked PMIDs.
  Examples: search_geo("SCALLOP"), search_geo("IBD RNA-seq", organism="Homo sapiens")
- get_geo_dataset: Full metadata for a known GSE/GDS accession (e.g. GSE66360).
- get_geo_series_relations: PubMed IDs linked to a dataset — useful for tracing
  publications associated with a series.

TYPICAL WORKFLOW: search_geo(query, organism) → get_geo_dataset(accession) for details.""",
)

logging.getLogger("httpx").setLevel(logging.WARNING)

_ssl_ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
_client = httpx.AsyncClient(
    base_url=BASE,
    timeout=60,
    verify=_ssl_ctx,
    headers={"Accept": "application/json"},
)


async def _get(path: str, **params: Any) -> Any:
    params = {k: v for k, v in params.items() if v is not None and v != ""}
    r = await _client.get(path, params=params)
    r.raise_for_status()
    try:
        return r.json()
    except ValueError:
        return r.text


def _build_query(query: str, organism: str | None) -> str:
    parts = [query] if query else []
    if organism:
        parts.append(f'"{organism}"[Organism]')
    return " AND ".join(parts)


def _parse_summary(uid: str, rec: dict) -> dict:
    # NCBI field names vary in casing across API versions — check both
    def _f(*keys: str) -> Any:
        for k in keys:
            v = rec.get(k)
            if v is not None:
                return v
        return None

    return {
        "accession": _f("Accession", "accession"),
        "uid": uid,
        "entry_type": _f("entryType", "entrytype"),
        "title": _f("title"),
        "summary": _f("summary"),
        "organism": _f("taxon"),
        "n_samples": _f("n_samples"),
        "platform_accession": _f("GPL"),
        "pubmed_ids": _f("PubMedIds", "pubmedids") or [],
        "update_date": _f("UpdateDate", "updatedate"),
    }


async def _uids_to_records(uids: list[str]) -> list[dict]:
    if not uids:
        return []
    data = await _get("/esummary.fcgi", db="gds", id=",".join(uids), retmode="json")
    result = data.get("result", {})
    return [_parse_summary(uid, result[uid]) for uid in uids if uid in result]


def _to_markdown_table(records: list[dict]) -> str:
    header = "| Accession | Title | Organism | N | Platform | PMIDs |"
    sep    = "|-----------|-------|----------|---|----------|-------|"
    rows = []
    for r in records:
        title = (r.get("title") or "")[:80].replace("|", "/")
        pmids = ", ".join(str(p) for p in (r.get("pubmed_ids") or []))
        rows.append(
            f"| {r.get('accession','')} "
            f"| {title} "
            f"| {r.get('organism','')} "
            f"| {r.get('n_samples','')} "
            f"| {r.get('platform_accession','')} "
            f"| {pmids} |"
        )
    return "\n".join([header, sep] + rows)



@mcp.tool()
async def search_geo(
    query: str,
    organism: str | None = None,
    entry_type: Literal["GSE", "GPL", "GDS", "any"] = "GSE",
    max_results: int = 200,
) -> dict:
    """Search GEO datasets by keyword with optional organism filter.

    Args:
        query: Free-text search (e.g. "IBD RNA-seq", "heart failure microarray").
        organism: Species name for exact filtering (e.g. "Homo sapiens", "Mus musculus").
        entry_type: Dataset type — GSE (series, most common), GPL (platform), GDS (curated), any.
        max_results: Maximum results to return (default 200).

    Returns both a markdown summary table and the full raw results list.
    """
    term = _build_query(query, organism)
    search = await _get(
        "/esearch.fcgi",
        db="gds",
        term=term,
        retmax=min(max_results * 2 if entry_type != "any" else max_results, 10000),
        retmode="json",
    )
    id_list: list[str] = search.get("esearchresult", {}).get("idlist", [])
    total_found: str = search.get("esearchresult", {}).get("count", "0")

    records = await _uids_to_records(id_list)

    if entry_type != "any":
        records = [r for r in records if (r.get("entry_type") or "").upper() == entry_type.upper()]

    records = records[:max_results]

    return {
        "query": term,
        "total_in_geo": int(total_found),
        "returned": len(records),
        "table": _to_markdown_table(records),
        "results": records,
    }


@mcp.tool()
async def get_geo_dataset(accession: str) -> dict:
    """Full metadata for a GEO dataset by accession (e.g. GSE66360, GDS1234).

    Returns accession, title, summary, organism, sample count, platform, PMIDs, and update date.
    """
    search = await _get(
        "/esearch.fcgi",
        db="gds",
        term=f"{accession}[Accession]",
        retmax=1,
        retmode="json",
    )
    id_list: list[str] = search.get("esearchresult", {}).get("idlist", [])
    if not id_list:
        return {"error": f"Accession '{accession}' not found in GEO"}
    records = await _uids_to_records(id_list[:1])
    return records[0] if records else {"error": "Could not parse record"}


@mcp.tool()
async def get_geo_series_relations(accession: str) -> dict:
    """Get PubMed IDs and related GEO records linked to a GSE accession.

    Useful for tracing which publications deposited or cite a dataset.
    """
    search = await _get(
        "/esearch.fcgi",
        db="gds",
        term=f"{accession}[Accession]",
        retmax=1,
        retmode="json",
    )
    id_list: list[str] = search.get("esearchresult", {}).get("idlist", [])
    if not id_list:
        return {"error": f"Accession '{accession}' not found"}

    uid = id_list[0]
    links = await _get(
        "/elink.fcgi",
        dbfrom="gds",
        db="pubmed",
        id=uid,
        retmode="json",
    )

    pubmed_ids: list[str] = []
    for link_set in links.get("linksets", []):
        for db_link in link_set.get("linksetdbs", []):
            if db_link.get("dbto") == "pubmed":
                pubmed_ids.extend(str(x) for x in db_link.get("links", []))

    return {
        "accession": accession,
        "geo_uid": uid,
        "linked_pubmed_ids": pubmed_ids,
    }


if __name__ == "__main__":
    mcp.run()

---
name: dataset-search
description: >-
  Use this skill whenever the user wants to search or discover publicly available
  datasets in GEO (NCBI Gene Expression Omnibus), PRIDE, or ProteomeXchange.
  Triggers include: "search GEO for", "find datasets on", "query PRIDE", "look
  up public datasets", "find RNA-seq / proteomics / microarray datasets", or any
  request to discover publicly available datasets. Also use proactively when the
  user's task implies a public dataset search (e.g. "what IBD datasets are
  available", "find transcriptomic data for heart failure").
---

# Public Dataset Search

## 1. Database routing

Choose the database based on what the user is looking for. Do **not** search multiple databases unless explicitly asked.

| Data type | Use |
|-----------|-----|
| RNA-seq, microarray, ChIP-seq, ATAC-seq, scRNA-seq, gene expression | **GEO** (`mcp__geo__search_geo`) |
| Proteomics / mass-spec — PRIDE-hosted only, rich disease/tissue filters | **PRIDE** (`mcp__pride__find_datasets`) |
| Proteomics / mass-spec — cross-repository (PRIDE, MassIVE, jPOST, iProX) | **ProteomeXchange** (`mcp__proteomexchange__find_datasets`) |

If the data type is ambiguous (e.g. user says "omics" or "multi-omics"), ask which database to search.

## 2. Species / organism default

- If the user names a species ("mouse", "mice", "bacteria"), translate it and use it as a filter
- If no species is mentioned, **default to `"Homo sapiens"`**
- Common mappings: "human" → `"Homo sapiens"` · "mouse" / "mice" → `"Mus musculus"` · "rat" → `"Rattus norvegicus"`
- Species filters in ProteomeXchange must use the full Latin name

## 3. GEO call rules

- Always `max_results=200` — never lower without explicit user request
- The tool returns `table` (markdown) and `results` (raw list of dicts); use both

## 4. PRIDE call rules

- Call `mcp__pride__list_filter_values` **first** when filtering by disease, tissue, or organism to get exact vocabulary
- Then call `mcp__pride__find_datasets` with those exact values

## 5. ProteomeXchange call rules

- Call `mcp__proteomexchange__list_filter_values` first to validate species / instrument names
- The tool returns `rows` (full raw list) and aggregated summary stats; use both

---

## 6. Default console output (always shown, no file written)

After every search, display in chat:

**a) Text summary paragraph** covering:
- Total datasets found in the database vs how many were returned
- Breakdown by organism / species
- Breakdown by tissue / sample type (if available)
- Date range of the datasets (oldest → newest)
- How many have linked PMIDs

**b) Summarized markdown table** — use database-specific columns (only show columns with actual data):

**GEO** (`n_samples` field available):
| Accession | Title | Organism | N Samples | Platform | PMIDs |
|-----------|-------|----------|-----------|----------|-------|

**PRIDE** (no sample count in API — use available fields):
| Accession | Title | Organisms | Diseases | Tissue | Instruments |
|-----------|-------|-----------|----------|--------|-------------|

**ProteomeXchange** (no sample count available across repositories):
| Accession | Title | Species | Tissue | Instrument | Repository | Date |
|-----------|-------|---------|--------|------------|------------|------|

Truncate long titles to ~80 characters. List up to `returned` rows.

---

## 7. File output (only when user asks to save / export)

| User intent | Action |
|-------------|--------|
| "save results", "export CSV", "raw data" | Write `results` / `rows` from the tool response to `~/Downloads/<db>_<query>_<timestamp>.csv` |
| "save summary", "export summary table" | Write the summarized markdown table to `~/Downloads/<db>_<query>_summary_<timestamp>.md` (or `.csv` if user prefers) |
| Both | Write both files; report both paths in chat |

Use the `Write` tool to create files. Do **not** re-call the MCP tool just to write a file — use the data already in the tool response.

**CSV columns:**

- GEO: `accession, uid, entry_type, title, organism, n_samples, platform_accession, pubmed_ids, update_date, summary`
- ProteomeXchange / PRIDE: `accession, title, repository, species, tissueType, instrument, announceDate, publications, keywords, matchedTerms, url`

When writing `pubmed_ids` or `matchedTerms` (list fields), join with `"; "` as a single string.

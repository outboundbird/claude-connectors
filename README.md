# Omics dataset connectors (MCP servers + skill)

This repo contains [Model Context Protocol](https://modelcontextprotocol.io) servers and a Claude skill for discovering public omics datasets. Together they let an AI agent search GEO, PRIDE, and ProteomeXchange in plain language.

| Component | Type | Data source | Best for |
|---|---|---|---|
| [`pride-mcp`](pride-mcp/) | MCP server | [PRIDE Archive](https://www.ebi.ac.uk/pride/) REST API v3 (EBI) | Precise search of PRIDE proteomics datasets using curated **disease, organism, tissue, instrument, experiment-type and quantification** tags. Also project files, SDRF, protein-to-project lookup, and archive statistics. |
| [`proteomexchange-mcp`](proteomexchange-mcp/) | MCP server | [ProteomeCentral PROXI API](https://proteomecentral.proteomexchange.org/PROXI.php) | **All ProteomeXchange repositories** (PRIDE, MassIVE, jPOST, iProX, PeptideAtlas, PanoramaPublic). Free-text disease search with synonyms, full result lists. |
| [`geo-mcp`](geo-mcp/) | MCP server | [NCBI GEO E-utilities](https://www.ncbi.nlm.nih.gov/geo/) | Transcriptomics: RNA-seq, microarray, ChIP-seq, ATAC-seq, scRNA-seq. |
| [`dataset-search`](dataset-search/) | Claude skill | — | Instructs the agent: which MCP to call, species defaults, console summary format, and when/how to export files. |

All MCP servers are read-only, call public APIs, and need no credentials.

---

## Architecture

The MCPs return data only — they never write files. The `dataset-search` skill controls all output decisions:

```
User prompt
    → dataset-search skill loads
        → routes to correct MCP (GEO / PRIDE / ProteomeXchange)
        → enforces max_results, species default (Homo sapiens if unspecified)
        → shows text summary + markdown table in console
        → writes CSV/MD files only when user asks (via Write tool)
```

---

## Which one to use

- **Transcriptomics (RNA-seq, microarray, ChIP-seq, scRNA-seq)**: GEO
- **Proteomics — PRIDE only, with rich disease/tissue tags**: PRIDE
- **Proteomics — all repositories**: ProteomeXchange
- **Don't know which**: the skill handles routing based on data type in the prompt

---

## Setup

### Requirements
- [uv](https://docs.astral.sh/uv/). It installs Python dependencies into each server's `.venv` automatically.
- Python 3.10+ (uv can fetch it if missing).
- [Claude Code](https://claude.com/claude-code), or any other MCP client.

### Install

```bash
git clone <this-repo-url> connectors
cd connectors
```

Register each MCP server with Claude Code:

```bash
claude mcp add pride --scope user -- <absolute-path-to-uv> --directory <absolute-path>/connectors/pride-mcp run server.py
claude mcp add proteomexchange --scope user -- <absolute-path-to-uv> --directory <absolute-path>/connectors/proteomexchange-mcp run server.py
claude mcp add geo --scope user -- <absolute-path-to-uv> --directory <absolute-path>/connectors/geo-mcp run server.py
```

Or use a `.mcp.json` at the project root:
```json
{
  "mcpServers": {
    "geo": {
      "command": "<absolute-path>/connectors/geo-mcp/.venv/Scripts/python.exe",
      "args": ["<absolute-path>/connectors/geo-mcp/server.py"]
    },
    "pride": { ... },
    "proteomexchange": { ... }
  }
}
```

Find uv's path with `where uv` (Windows) or `which uv` (macOS/Linux).

### Install the skill

Copy `dataset-search/` to your Claude skills directory:

```bash
# Windows
cp -r dataset-search "%USERPROFILE%\.claude\skills\dataset-search"

# macOS / Linux
cp -r dataset-search ~/.claude/skills/dataset-search
```

The skill loads automatically in new sessions. Invoke explicitly with `/dataset-search`.

Check servers connected:
```bash
claude mcp list
```

All three should show `✔ Connected`. Start a **new session** after connecting — tools load at session start.

---

## Usage

Ask in plain language. The skill handles routing automatically.

```
Find RNA-seq datasets for heart failure in GEO.
Find RNA-seq datasets for IBD in GEO, blood samples, and save to CSV.
Find proteomic datasets for Alzheimer's disease in PRIDE.
Find proteomic datasets for IBD across all repositories.
Save those results as CSV.
Save the summary as a markdown file.
```

The agent will:
1. Route to the correct database
2. Default to `Homo sapiens` if no species is specified
3. Show a text summary (total found, organism breakdown, tissue breakdown, date range) + markdown table in chat
4. Write CSV/markdown files only when you ask

---

## Tool reference

### `geo`

| Tool | What it does |
|---|---|
| `search_geo` | Free-text search with optional organism filter. Returns `table` (markdown) and `results` (raw list). Filters: `organism`, `entry_type` (GSE/GPL/GDS/any), `max_results` (default 200). |
| `get_geo_dataset` | Full metadata for a GSE/GDS accession. |
| `get_geo_series_relations` | PubMed IDs linked to a dataset. |

**Result fields:** accession, uid, entry_type, title, organism, n_samples, platform_accession, pubmed_ids, update_date, summary.

### `proteomexchange`

| Tool | What it does |
|---|---|
| `find_datasets` | Free-text search per term (merged and de-duplicated). Returns aggregated summary stats plus `rows` (full raw list). Filters: `species`, `instrument`, `repository`, `keywords`, `year`, `sdrf`, `modification`, `contact`. |
| `list_filter_values` | Valid filter values with dataset counts, optionally scoped by search term. |
| `get_dataset` | Full record for a PXD/MSV/JPST/IPX/PASS accession. |
| `list_dataset_files` | File URLs grouped by type (raw, search-engine output, results…). |
| `search_libraries` | Spectral libraries indexed by ProteomeCentral. |

**Row fields:** accession, title, repository, species, tissueType, tissueSource, instrument, announceDate, publications, keywords, matchedTerms, url.

Tissue type is PRIDE's curated organism-part annotation for PRIDE datasets, or inferred from title/keywords for others. Set `include_tissue=False` for very large searches.

### `pride`

| Tool | What it does |
|---|---|
| `find_datasets` | Keyword + curated filters: `organism`, `disease`, `tissue`, `instrument`, `experiment_type`, `quantification_method`. |
| `list_filter_values` | Exact tag values with counts for all curated fields. |
| `autocomplete` | Suggestions for a partial term. |
| `get_project` | Full project metadata: protocols, references, sample attributes. |
| `list_project_files` | Paged file list with FTP/Aspera URLs. |
| `count_files_by_type` | File counts by category (RAW, PEAK, RESULT, SEARCH…). |
| `get_sdrf` | Links to SDRF sample-metadata files. |
| `get_file_checksums` | MD5 checksums for all project files. |
| `similar_projects` / `reanalyses` | Related datasets and reuse publications. |
| `search_proteins` / `get_protein` | PRIDE projects reporting a UniProt accession. |
| `get_stats` / `dataset_status` | Archive statistics and public/private status. |

---

## Tips and gotchas

- **Exact names matter for structured filters.** Call `list_filter_values` first to get valid vocabulary.
  - PRIDE: `Homo sapiens (human)`, `Mus musculus (mouse)`
  - ProteomeXchange: `Homo sapiens`, `Mus musculus`
- **Diseases are free text in ProteomeXchange.** Pass synonyms as separate `search_terms`. Keep terms short — `"IBD colitis"` matches nothing, but `"IBD"` and `"colitis"` each match.
- **PRIDE disease tags are inconsistent** (`Crohn's disease` vs `Crohn disease`). Include every variant from `list_filter_values`.
- **Species default.** If you don't mention a species, the skill defaults to `Homo sapiens`. Say "mouse" or "Mus musculus" to override.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `CERTIFICATE_VERIFY_FAILED` | Corporate TLS proxy (e.g. Zscaler). All servers use `truststore` to read the OS certificate store. If it still fails, the proxy's root CA is missing from the OS store. |
| `'uv' is not recognized` | Client starts servers without your PATH. Register with the absolute path to `uv`. |
| Server connected but agent ignores it | Tools only load in a new session. Start one and name the source in the prompt, or use `/dataset-search`. |
| Blocked in VS Code Copilot | Some organizations restrict Copilot to an approved MCP registry. Use Claude Code instead. |

---

## Development

```text
connectors/
├── pride-mcp/
│   ├── server.py        # all tools, one file
│   ├── pyproject.toml
│   └── uv.lock
├── proteomexchange-mcp/
│   ├── server.py
│   ├── pyproject.toml
│   └── uv.lock
├── geo-mcp/
│   ├── server.py
│   ├── pyproject.toml
│   └── uv.lock
└── dataset-search/
    └── SKILL.md         # Claude skill — controls agent routing and output
```

All servers use the MCP Python SDK v2 (`from mcp.server.mcpserver import MCPServer`). Tools are `async` functions decorated with `@mcp.tool()`; docstrings and type hints become the schema the agent sees.

Test interactively (in a server folder):
```bash
uv run mcp dev server.py
```

Changes take effect in **new** client sessions.

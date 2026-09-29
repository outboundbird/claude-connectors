# Proteomics connectors (MCP servers)

This repo contains two [Model Context Protocol](https://modelcontextprotocol.io) servers. They let an AI agent such as Claude Code search public proteomics (mass-spectrometry) data repositories, for example to find datasets for a disease, species or tissue.

| Server | Data source | Best for |
|---|---|---|
| [`pride-mcp`](pride-mcp/) | [PRIDE Archive](https://www.ebi.ac.uk/pride/) REST API v3 (EBI) | Precise search of PRIDE datasets using curated **disease, organism, tissue, instrument, experiment-type and quantification** tags. Also project files, sample metadata (SDRF), protein-to-project lookup, and archive statistics. |
| [`proteomexchange-mcp`](proteomexchange-mcp/) | [ProteomeCentral PROXI API](https://proteomecentral.proteomexchange.org/PROXI.php) | **All ProteomeXchange repositories** (PRIDE, MassIVE, jPOST, iProX, PeptideAtlas, PanoramaPublic). Free-text disease search with synonyms, and **full result lists exported to CSV**. |

Both are read-only, call public APIs, and need no credentials.

---

## Which one to use

- **"Find datasets for disease X"**: use both. `proteomexchange` has wider coverage across repositories; `pride` is more precise for PRIDE datasets because diseases are curated tags there, not free text.
- **A spreadsheet of every matching dataset**: `proteomexchange.find_datasets`, which writes a CSV.
- **Tissue, experiment type or quantification method filters** (e.g. colon + TMT): `pride`. ProteomeXchange has no such fields.
- **Which datasets contain protein P04637**: `pride.search_proteins`. ProteomeCentral doesn't implement protein, peptide or PSM search.
- **Files, SDRF or checksums for a PXD accession**: `pride` for PRIDE-hosted datasets. `proteomexchange.list_dataset_files` works for any repository.

---

## Setup

### Requirements
- [uv](https://docs.astral.sh/uv/). It installs Python dependencies into each server's `.venv` automatically.
- Python 3.10+ (uv can fetch it if missing).
- [Claude Code](https://claude.com/claude-code), or any other MCP client.

### Install and register with Claude Code
```bash
git clone <this-repo-url> connectors
cd connectors
```
```bash
claude mcp add pride --scope user -- <absolute-path-to-uv> --directory <absolute-path>/connectors/pride-mcp run server.py
```
```bash
claude mcp add proteomexchange --scope user -- <absolute-path-to-uv> --directory <absolute-path>/connectors/proteomexchange-mcp run server.py
```
- Find uv's path with `where uv` (Windows) or `which uv` (macOS/Linux). An absolute path is safer than plain `uv`, because some clients start servers without your shell's PATH.
- `--scope user` makes the servers available in every project. Use `--scope project` to write a shareable `.mcp.json` in one project instead.
- The first launch runs `uv sync`, which may take a few seconds.

Check that both connected:
```bash
claude mcp list
```
Both should show `✔ Connected`. Then **start a new Claude Code session**, because tools load when a session starts. They appear as `mcp__pride__*` and `mcp__proteomexchange__*`.

### Other MCP clients
Use the same command and arguments. For example, in a client that uses the common `mcpServers` JSON format:
```json
{
  "mcpServers": {
    "pride": {
      "command": "<absolute-path-to-uv>",
      "args": ["--directory", "<absolute-path>/connectors/pride-mcp", "run", "server.py"]
    },
    "proteomexchange": {
      "command": "<absolute-path-to-uv>",
      "args": ["--directory", "<absolute-path>/connectors/proteomexchange-mcp", "run", "server.py"]
    }
  }
}
```

---

## Usage

Ask in plain language. Naming the source makes tool selection reliable.

```
Search ProteomeXchange for inflammatory bowel disease datasets and save the CSV to C:\Users\me\Projects\IBD.
Find human Alzheimer's disease datasets on ProteomeXchange acquired with timsTOF instruments.
Use PRIDE to find ulcerative colitis datasets from human colon tissue quantified with TMT.
What diseases have the most human liver datasets in PRIDE?
List the raw files and the SDRF for PXD036591.
Which PRIDE projects report protein P04637 (TP53)?
Find IBD datasets in both PRIDE and ProteomeXchange and merge them by accession.
```

### Typical workflows

**Cross-repository disease search (ProteomeXchange)**
1. `find_datasets(search_terms=["ulcerative colitis", "Crohn", "inflammatory bowel disease", "IBD"], species="Homo sapiens", output_path="C:/Users/me/Projects/IBD")`
2. The chat gets a short summary: unique count, matches per term, breakdowns by repository, year, species and instrument, and the 10 newest datasets.
3. The full list is saved as a CSV at the location you chose (see [CSV export](#csv-export-proteomexchange)).
4. Drill into a dataset with `get_dataset("PXD036591")` or `list_dataset_files("PXD036591", file_type="raw")`.

**Precise search by curated tags (PRIDE)**
1. `list_filter_values(keyword="colitis", field="diseases")` returns the exact tag names, e.g. `Ulcerative colitis`, `Crohn's disease`, `Crohn disease`.
2. `find_datasets(disease=["Ulcerative colitis"], organism=["Homo sapiens (human)"], tissue=["Colon"], quantification_method=["TMT"])`
3. Explore further with `get_project`, `list_project_files`, `get_sdrf` and `similar_projects`.

---

## Tool reference

### `proteomexchange`

| Tool | What it does |
|---|---|
| `find_datasets` | Runs one free-text search per term, merges and de-duplicates the results, **fetches every page**, writes a CSV, and returns a summary. Filters: `species`, `instrument`, `repository`, `keywords`, `year`, `sdrf`, `modification`, `contact`. Options: `max_per_term` (default 2000), `preview_rows` (default 10), `output_path` (absolute folder or `.csv` path). |
| `list_filter_values` | Valid filter values with dataset counts for species, instrument, repository, keywords, year, SDRF status and file-count bins, optionally scoped by a search term. |
| `get_dataset` | Full record for a `PXD`/`MSV`/`JPST`/`IPX`/`PASS` accession: description, species, instruments, modifications, publications, contacts, links, SDRF status. |
| `list_dataset_files` | File URLs grouped by type (raw, search-engine output, results…). Optional `file_type` substring filter. |
| `search_libraries` | Spectral libraries indexed by ProteomeCentral. |

#### CSV export (ProteomeXchange)
- **Location (you choose it):** say where in your prompt, e.g. "…save the CSV to `C:\Users\me\Projects\IBD`". The agent passes this as `output_path`, which must be absolute. It can be:
  - a **folder**: the file is named `px_<search-terms>_<YYYYMMDD-HHMMSS>.csv`
  - a **full file path** ending in `.csv`: used as given
- **Fallback:** if you don't give a location, the `PX_EXPORT_DIR` environment variable is used, if set. Otherwise no CSV is written: you get only the summary, and the agent is told to ask you where to save it.
- **Encoding:** UTF-8 with a byte-order mark, so Excel shows accented names correctly.
- **Columns:**

| Column | Meaning |
|---|---|
| `accession` | ProteomeXchange ID (e.g. PXD036591) |
| `title` | Dataset title |
| `repository` | Hosting repository (PRIDE, MassIVE, iProX…) |
| `species` | Species, comma-separated |
| `instrument` | MS instrument(s) |
| `announceDate` | Public release date |
| `publications` | Citations or DOIs, HTML stripped |
| `labHead` | Lab head / PI |
| `keywords` | Submitter keywords |
| `rawFiles` / `totalFiles` | Number of raw files / all files. These are separate integer columns because Excel turns values like `1/2` into dates. |
| `sdrf` | SDRF (sample metadata) status |
| `matchedTerms` | Which of your search terms matched, separated by `; ` |
| `url` | ProteomeCentral dataset page |

### `pride`

| Tool | What it does |
|---|---|
| `find_datasets` | Keyword search plus curated filters: `organism`, `disease`, `tissue`, `instrument`, `experiment_type`, `quantification_method`, `software`, `project_keyword`, and a publication-year range. Returns compact records and `totalMatches`. |
| `list_filter_values` | Exact tag values with counts (organisms, diseases, organismsPart, instruments, experimentTypes, quantificationMethods, softwares, …), optionally scoped by keyword, organism, disease or tissue. |
| `autocomplete` | Suggestions for a partial term. |
| `get_project` | Full project metadata: protocols, references, sample attributes. |
| `list_project_files` | Paged file list with category, size and FTP/Aspera URLs. Optional file-name filter. |
| `count_files_by_type` | File counts by category (RAW, PEAK, RESULT, SEARCH, …). |
| `get_sdrf` | Links to the project's SDRF sample-metadata files. |
| `get_file_checksums` | MD5 checksums for all project files. |
| `get_file` | Details for one file accession. |
| `similar_projects` | Projects with similar metadata. |
| `reanalyses` | Datasets or publications that reanalysed the project. |
| `search_proteins` / `get_protein` | PRIDE projects that report a UniProt accession. |
| `get_stats` | Archive statistics, e.g. `submissions-monthly`, `SUBMISSIONS_PER_YEAR`. |
| `dataset_status` | Whether an accession is PUBLIC or PRIVATE. |

---

## Tips and gotchas

- **Exact names matter.** Structured filters only match exact values, and a wrong value silently returns 0 results. Call `list_filter_values` first:
  - PRIDE uses organism names like `Homo sapiens (human)` and `Mus musculus (mouse)`.
  - ProteomeXchange uses `Homo sapiens` and `Mus musculus`.
- **Diseases are free text in ProteomeXchange.** Pass synonyms and abbreviations as separate `search_terms`. Each term is matched as a phrase, so keep terms short: `"IBD colitis"` matches nothing, while `"IBD"` and `"colitis"` each match.
- **PRIDE disease tags are inconsistent** (`Crohn's disease` vs `Crohn disease`, `Inflammatory bowel disease` vs `Inflammatory bowel disease 1`). Include every variant that `list_filter_values` shows.
- **Coverage differs.** In one IBD search, PRIDE tags matched 58 datasets and ProteomeXchange free-text search found 137 across five repositories. Use both for a complete picture.
- **Omitted endpoints:** PRIDE's bulk-download endpoints (`/projects/all`, `/files/all`, `/projects/download*`) return too much data for an agent. ProteomeCentral's `/proteins`, `/peptidoforms` and `/psms` return 501 (not implemented).

### Making the agent pick these tools reliably
The agent chooses tools by matching your request against the tool names, descriptions and server instructions. To make that more reliable:
- Name the source in your prompt ("search ProteomeXchange…", "use PRIDE…").
- Add a rule to `CLAUDE.md` (per project, or `~/.claude/CLAUDE.md` for all projects):
  ```markdown
  For proteomics dataset searches: use the `proteomexchange` MCP for cross-repository coverage and CSV
  export; use `pride` for PRIDE-specific disease/tissue/quantification tags, files, SDRF and protein lookups.
  When asked for "all" datasets, query both and merge by accession.
  ```

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `CERTIFICATE_VERIFY_FAILED` / self-signed certificate | A corporate TLS-inspecting proxy (e.g. Zscaler). Both servers use [`truststore`](https://pypi.org/project/truststore/), which trusts the OS certificate store, so this should already be handled. If it still fails, the proxy's root CA is missing from the OS store. |
| `'uv' is not recognized` | The client starts servers without your PATH. Register with the absolute path to `uv`. |
| Server `✔ Connected` but the agent doesn't use it | The tools only load in a new session. Start one, and name the source in the prompt. |
| Tool output too large | `proteomexchange.find_datasets` returns only a summary and puts the full list in the CSV. For `pride.find_datasets`, lower `page_size`. |
| Blocked in VS Code Copilot | Some organizations restrict Copilot to MCP servers from an approved registry (`chat.mcp.access = registry`), and Copilot's agent sandbox may hide `uv`/Python. Use Claude Code instead, or ask IT to add the servers to the registry. |
| Can't delete or move a server folder | Running sessions keep the server's `.venv` in use. Close those sessions first. |

---

## Development

```text
connectors/
├── pride-mcp/
│   ├── server.py        # all tools, one file
│   ├── pyproject.toml   # deps: mcp[cli]>=2.2, httpx, truststore
│   └── uv.lock
└── proteomexchange-mcp/
    ├── server.py
    ├── pyproject.toml
    └── uv.lock
```

- Servers use the MCP Python SDK v2 (`from mcp.server.mcpserver import MCPServer`). A tool is an `async` function decorated with `@mcp.tool()`; its docstring and type hints become the description and input schema the agent sees.
- To test interactively, run this in the server's folder to open the MCP Inspector:
  ```bash
  uv run mcp dev server.py
  ```
- Changes take effect in **new** client sessions, since running sessions keep the old server process.

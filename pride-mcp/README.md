# pride-mcp

MCP server for the [PRIDE Archive](https://www.ebi.ac.uk/pride/) REST API v3 (proteomics datasets).

## Install / register (user scope)
```bash
claude mcp add pride --scope user -- uv --directory C:/Users/e0482362/Workspace/Claude/connectors/pride-mcp run server.py
```

## Tools
- `list_filter_values`: valid organism/disease/tissue/instrument/... values with counts. Call this first; filters need exact names.
- `find_datasets`: keyword plus structured criteria search.
- `autocomplete`, `get_project`, `similar_projects`, `reanalyses`, `dataset_status`
- `list_project_files`, `count_files_by_type`, `get_sdrf`, `get_file_checksums`, `get_file`
- `search_proteins`, `get_protein`, `get_stats`

## Notes
- Uses the OS trust store (`truststore`), so it works behind corporate TLS inspection.
- Debug with the MCP Inspector: `uv run mcp dev server.py`.

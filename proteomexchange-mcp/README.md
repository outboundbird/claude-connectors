# proteomexchange-mcp

MCP server for the [ProteomeCentral PROXI API](https://proteomecentral.proteomexchange.org/PROXI.php). It searches ProteomeXchange datasets across PRIDE, MassIVE, jPOST, iProX, PeptideAtlas and PanoramaPublic.

## Register (Claude Code, user scope)
```bash
claude mcp add proteomexchange --scope user -- C:/Users/e0482362/.local/bin/uv.exe --directory C:/Users/e0482362/Workspace/Claude/connectors/proteomexchange-mcp run server.py
```

## Tools
- `find_datasets`: free-text `search_terms`, where each term is searched separately and the results are merged. Pass disease names plus their synonyms. Filters for species, instrument, repository, keywords, year and SDRF status.
- `list_filter_values`: facet values with counts, optionally scoped by a search term.
- `get_dataset`, `list_dataset_files`, `search_libraries`

## Notes
- PROXI has no structured disease field. Disease matching is free text over titles, descriptions and keywords.
- ProteomeCentral doesn't implement `/proteins`, `/peptidoforms` or `/psms` (they return 501), so those aren't exposed.
- Uses the OS trust store (`truststore`) so it works behind Zscaler.

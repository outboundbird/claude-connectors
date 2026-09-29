# proteomexchange-mcp

MCP server for the [ProteomeCentral PROXI API](https://proteomecentral.proteomexchange.org/PROXI.php). It searches ProteomeXchange datasets across PRIDE, MassIVE, jPOST, iProX, PeptideAtlas and PanoramaPublic.

## Register (Claude Code, user scope)
```bash
claude mcp add proteomexchange --scope user -- C:/Users/e0482362/.local/bin/uv.exe --directory C:/Users/e0482362/Workspace/Claude/connectors/proteomexchange-mcp run server.py
```

## Tools
- `find_datasets`: free-text `search_terms`, where each term is searched separately and the results are merged. Pass disease names plus their synonyms. Filters for species, instrument, repository, keywords, year and SDRF status.
  - Fetches every page (up to `max_per_term`, default 2000) and writes all unique datasets to a CSV with columns `accession, title, repository, species, instrument, announceDate, publications, labHead, keywords, rawFiles, totalFiles, sdrf, matchedTerms, url`, plus `tissueType` and `tissueSource` after `species`. The tissue is curated from PRIDE where available, otherwise inferred from keywords in the text (`include_tissue=False` skips this). The file counts are split into two integer columns because Excel turns values like `1/2` into dates.
  - You choose where the CSV goes, with `output_path`: an absolute folder path (a timestamped file name is generated) or a full `.csv` file path. The fallback is `$PX_EXPORT_DIR`. If neither is set, no CSV is written; you get only the summary, and the agent is told to ask you where to save. It's UTF-8 with a byte-order mark so Excel opens it correctly.
  - Returns only a summary: CSV path, unique count, matches per term, breakdowns by repository, year, species and instrument, and the 10 newest datasets.
- `list_filter_values`: facet values with counts, optionally scoped by a search term.
- `get_dataset`, `list_dataset_files`, `search_libraries`

## Notes
- PROXI has no structured disease field. Disease matching is free text over titles, descriptions and keywords.
- ProteomeCentral doesn't implement `/proteins`, `/peptidoforms` or `/psms` (they return 501), so those aren't exposed.
- Uses the OS trust store (`truststore`) so it works behind Zscaler.

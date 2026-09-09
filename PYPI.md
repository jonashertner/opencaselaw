# swiss-caselaw-scrapers

The Python distribution for [OpenCaseLaw](https://opencaselaw.ch), an open
pipeline for collecting, normalising, searching, and analysing published Swiss
federal and cantonal court decisions.

The package ships the scraper registry and pipeline, quality-control tools,
search stack, REST/MCP server modules, and two command-line entry points:
`swiss-caselaw` and `swiss-caselaw-gap-report`.

## Install

Use a virtual environment and install the capabilities you need:

```console
python -m pip install swiss-caselaw-scrapers
python -m pip install "swiss-caselaw-scrapers[api]"
```

This is an alpha release of an actively developed research infrastructure
project. Scrapers make network requests to official publication systems when
you invoke them; review the selected source and applicable terms before a run.

## Documentation and provenance

- Project and public search: <https://opencaselaw.ch>
- Source and issue tracker: <https://github.com/jonashertner/opencaselaw>
- API documentation: <https://mcp.opencaselaw.ch/api/docs>
- Dataset: <https://huggingface.co/datasets/voilaj/swiss-caselaw>
- Project updates: <https://www.linkedin.com/company/opencaselaw-ch/>

Official releases are built from the public repository and published to PyPI
through GitHub Actions trusted publishing. The release workflow uses short-lived
OIDC credentials; the repository does not store a long-lived PyPI token.

## Licensing

The software is MIT-licensed. Published datasets are generally CC0; source-
specific exceptions and the governance/removal policy are documented in the
repository and on the project website.

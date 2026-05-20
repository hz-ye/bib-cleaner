# Bib Cleaner

Bib Cleaner is a small Python tool for normalizing BibTeX files copied from Google Scholar and similar sources. It is designed to preserve non-entry content such as comments and `@String` definitions while cleaning real bibliography entries.

## Caution

This tool does not look up DOI values for `@book` entries. Books can have DOIs, but Crossref book searches are often more ambiguous because of editions, chapters, edited volumes, publisher pages, and reprints. Book DOI lookup is intentionally left for manual review.

## Files

- `reference.bib`: input BibTeX file.
- `output_clean.bib`: cleaned BibTeX output.
- `cleaning_report.md`: report with applied changes, unresolved DOI lookups, duplicate entries, and URL risks.
- `main.py`: cleaning script.

## What It Does

- Normalizes arXiv entries by extracting the arXiv ID into `eprint`, adding `archivePrefix = {arXiv}`, removing `journal`, and removing `url`.
- Replaces `lastaccessed` with `urldate = {2026-01-13}` for `online` entries and URL-bearing non-arXiv `misc` entries.
- Looks up missing DOI values for `article`, `inproceedings`, and `incollection` entries through Crossref.
- Adds DOI values only when the Crossref match is high confidence.
- Treats a Crossref title as matching if either the full title or the part before `:` matches strongly.
- Reports duplicate keys and duplicate title/year groups without deleting them.
- Reports URL whitespace risks without validating URLs online.

## Requirements

- Python 3
- `requests`

Install the runtime dependency if needed:

```bash
python3 -m pip install requests
```

## Usage

Place the source bibliography at `reference.bib`, then run:

```bash
python3 main.py
```

The script writes `output_clean.bib` and `cleaning_report.md` in the same directory.

## Notes

- Duplicate entries are reported for manual review and are not automatically removed.
- URL risks are reported only; the script does not perform online URL validation.
- Crossref requests use the `HEADERS` value in `main.py`. Replace the placeholder email with a real address for better API etiquette.

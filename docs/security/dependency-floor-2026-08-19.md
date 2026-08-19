# Dependency floor and PDF-rendering hardening

This change raises dependency floors consumed by downstream worker exports and
makes the existing WeasyPrint mitigation explicit at the rendering boundary.

Native-Code-First Review:

- Need: remove vulnerable `pypdf`, `requests`, and `datasets` ranges and close
  the no-fix WeasyPrint presentational-hints path.
- Search: `pyproject.toml`, `src/local_deep_research/web/services/pdf_service.py`,
  and the existing SSRF regression tests.
- Native option: retain the existing `PDFService` and WeasyPrint API; set its
  native `presentational_hints` option explicitly.
- Gap: dependency ranges admitted vulnerable releases, and the safe default was
  not pinned by a regression test.
- Decision: configure the existing renderer and raise existing dependency
  floors; no new renderer or PDF route is introduced.
- Duplication check: one PDF service and one dependency lock remain.

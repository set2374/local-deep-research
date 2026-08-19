# Dependency floor and PDF-rendering hardening

This change raises dependency floors consumed by downstream worker exports and
makes the existing WeasyPrint mitigation explicit at the rendering boundary.

Native-Code-First Review:

- Need: remove vulnerable `pypdf` and `datasets` ranges, keep the existing
  patched-Requests resolution override compatible with `arxiv` metadata, and
  close the no-fix WeasyPrint presentational-hints path.
- Search: `pyproject.toml`, `src/local_deep_research/web/services/pdf_service.py`,
  and the existing SSRF regression tests.
- Native option: retain the existing `PDFService` and WeasyPrint API; set its
  native `presentational_hints` option explicitly.
- Gap: dependency ranges admitted vulnerable releases, Requests needs a resolver
  override because `arxiv` caps its published metadata, and the safe rendering
  default was not pinned by a regression test.
- Decision: configure the existing renderer, raise compatible dependency floors,
  and retain the existing Requests resolution override; no new renderer or PDF
  route is introduced.
- Duplication check: one PDF service and one dependency lock remain.

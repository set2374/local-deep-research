# Dependency floor and PDF-rendering hardening

This change raises dependency floors consumed by downstream worker exports,
makes the existing WeasyPrint mitigation explicit at the rendering boundary,
and gates the exact production lock export with both OSV-Scanner and pip-audit.

The exact combined Litigus integration revision was refreshed across the full
Python 3.12–3.14 lock target. `pip-audit` moved from 80 advisory rows across 19
packages to zero unmitigated advisories. The single remaining database record,
`PYSEC-2026-3412` in WeasyPrint 68.1, has no fixed release and is ignored only
at the audit invocation that documents the `presentational_hints=False`
compensating control. `tests/web/services/test_pdf_service.py` locks that call.

Native-Code-First Review:

- Need: remove vulnerable production dependency ranges, keep the existing
  patched-Requests resolution override compatible with `arxiv` metadata,
  close the no-fix WeasyPrint presentational-hints path, and prevent a clean
  lock from silently regressing.
- Search: `pyproject.toml`, `pdm.lock`, the existing OSV workflow,
  `src/local_deep_research/web/services/pdf_service.py`, and the existing
  SSRF/PDF regression tests.
- Native option: retain the existing `PDFService`, WeasyPrint API, PDM lock,
  and OSV workflow; configure the renderer and extend the existing dependency
  workflow with a lock-export pip-audit job.
- Gap: dependency ranges admitted vulnerable releases, OSV alone did not expose
  the same resolved-lock findings, Requests needs a resolver override because
  `arxiv` caps its published metadata, and the safe rendering default was not
  pinned by a regression test.
- Decision: configure the existing renderer, raise compatible published
  dependency floors, retain the existing Requests resolution override, and
  audit the PDM production export without invoking pip's resolver; no new
  renderer, lock format, or PDF route is introduced.
- Duplication check: one PDF service, one dependency lock, and one PR-triggered
  dependency workflow remain.

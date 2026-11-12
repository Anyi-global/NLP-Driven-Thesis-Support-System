# UNIZIK NLP-Driven Thesis Support Backend (Prototype)

A modular Flask REST API scaffold aligned with the submitted backend development plan and Chapters 1–3. It supports student, supervisor, and admin roles; thesis upload and parsing; configurable compliance checks; optional Gemini-based formative evaluation; feedback triage; revision history; exports; and audit logs.

## Important research and implementation notes

- This is a functional **prototype baseline**, not a validated final thesis system.
- `data/rules/unizik_rules.json` and `data/rules/manual_sections.jsonl` were seeded from the UNIZIK Manual of Style (2019) and the Computer Science departmental format. They are traceable starter checks, not proof of full compliance. Validate them against sample theses before reporting accuracy figures.
- **Source precedence.** The UNIZIK Manual of Style 2019 governs everything except the section structure of Chapters Three, Four and Five, which follows the Computer Science department format. So Chapter One/Two structure, preliminary pages, **double (2.0) line spacing**, **margins of 2.5 cm left and 2.0 cm right/top/bottom**, A4 and page numbering all come from the Manual; the department's own 1.5 spacing and 1-inch margins are not applied.
- **Compliance checks (28 rules).** Preliminary pages (presence, order, abstract length 300 MSc / 500 PhD, one paragraph; List of Abbreviations and Operational Definition of Terms are required because the Manual does not mark them "if any"); Chapter One and Two segments from the Manual ("where applicable" segments are not required); Chapter Three-Five sections from the department format ("(if any)" Chapter Four sections are not required); heading-number continuity; effective font, size and line spacing over body text only; margins in every section; A4; page numbers bottom right with Roman/Arabic styles; citation comma, `&` vs `and`, alphabetical order of citations cited together; citation-to-reference matching by author **and** year; uncited references; alphabetical reference order. Headings are matched by title, ignoring Word auto-numbering. Partial drafts (fewer than five chapters) get missing-chapter/reference findings downgraded to *low*.
- **PDF** submissions: text, structure, fonts, font sizes and paper size are measured. Margins, line spacing and page-number style are not; submit DOCX for those.
- **AI evaluation** sends the complete extracted thesis text to Gemini in one generation request, assessing chapter-level research quality and cross-chapter alignment together. A long thesis must fit within the selected model's context limit; if the request fails, the analysis note reports the error and compliance results remain available. Each finding must quote the thesis verbatim; findings whose quote is not found in the text are discarded as likely hallucinations. It is optional: without `GEMINI_API_KEY`/`GEMINI_MODEL`, the rule checks still run.
- **Model names expire.** Set `GEMINI_MODEL` (and `EMBEDDING_MODEL` for FAISS) to models listed at <https://ai.google.dev/gemini-api/docs/deprecations>. The former default `gemini-2.5-flash` is deprecated and may return 404; the app reports this clearly instead of failing silently.
- **Retrieval** uses FAISS + Gemini embeddings when `data/vector_store/index.faiss` exists (build it with `python scripts/build_vector_store.py`) and falls back to BM25 otherwise. The corpus is 38 passages condensed from the Manual and department format, each with a page reference; Manual passages for Chapters Three-Five are marked as content guidance only.
- **Supervisor review gate.** Rule-based findings are visible to the student immediately. AI findings are hidden from the student until the assigned supervisor accepts, edits or comments on them; rejected findings are hidden from everyone but staff. A student with no assigned supervisor will therefore never see AI findings.
- **Supervisor access.** Supervisors only see students an admin has assigned to them (`PUT /api/admin/students/<id>/supervisor`). Admins see everything.
- **Annotated DOCX export.** The export is a copy of the student's DOCX with real Word tracked changes (`<w:ins>`/`<w:del>`) for fixes that are safe to automate: `&` to `and` in citations, the missing comma before a citation year, Latin abbreviations (e.g. to 'for example'), ordinal suffixes on dates/positions and decade apostrophes. Chapters only; the reference list is never edited. Margins, paper size, spacing, missing sections, grammar and acronyms are NOT auto-fixed. Supervisor comments become Word comments on the chapter heading (needs python-docx >= 1.2). A feedback summary is appended. `?scope=accepted` applies only fixes the student accepted; fixes the supervisor rejected are never applied. Changes are proposals: accept or reject each in Word.
- Public registration creates student accounts only.

### Language engines (what spaCy, NLTK, LanguageTool and LangChain do here)
All four are optional (`pip install -r requirements-optional.txt`); without them the app uses regex/BM25 fallbacks and the analysis summary lists which engines ran.
- spaCy and NLTK are initialized when the Flask app starts, avoiding their cold-import delay on a student's first upload. LanguageTool remains lazy because it may download its Java server on first use.
- **spaCy** (blank English pipeline + sentencizer): sentence and token segmentation. Used to show the offending *sentence* in style findings and to count abstract words.
- **NLTK** (Porter stemmer): stemming for BM25 retrieval, so "citation"/"citations" match.
- **LanguageTool** (`LANGUAGETOOL_ENABLED=1`, needs Java): British-English spelling and grammar (rule `LANG-001`), because the Manual requires British English. Capitalised words, acronyms, tokens with digits and `data/rules/custom_dictionary.txt` are not reported as misspellings. If enabled but unavailable, the analysis note says so.
- **LangChain + FAISS** with Gemini embeddings: dense retrieval of Manual passages (build with `python scripts/build_vector_store.py`); falls back to plain FAISS, then BM25.
**These integrations were written and tested here with fakes standing in for the libraries** (the sandbox had no network), so the code that calls spaCy, NLTK, LanguageTool and LangChain has not been run against the real packages. Run `pytest -q` and one real thesis with the engines installed before relying on them.

### New Manual-based rules
`FMT-007` heading formats (chapter heading centred/bold/uppercase; level two Title Case; level three and four sentence case; level four italic; maximum four levels), `STY-001` Latin abbreviations, `STY-002` ordinals/decades/"ninety %", `STY-003` third person in Chapter Four, `ABBR-001` acronyms spelled out on first use, `LANG-001` British English (optional). A missing chapter in a full five-chapter draft is now flagged *high*.

### Endpoints for the thesis menus
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/student/summary` | My Reports summary cards |
| GET | `/api/student/progress` | Revision cycles and compliance change per version |
| GET | `/api/student/feedback` | Feedback inbox: items the supervisor accepted, edited or commented on |
| POST | `/api/student/theses/1/submit-for-review` | Send the report to the supervisor (needs an assigned supervisor) |
| GET | `/api/supervisor/assigned-theses` | Assigned students and their submissions |
| GET | `/api/supervisor/progress?student_id=1` | Progress for one or all assigned students |
| GET | `/api/supervisor/feedback/history` | Items already reviewed |
| POST | `/api/supervisor/feedback/bulk` | Accept/reject up to 100 items at once (all-or-nothing) |
| GET | `/api/admin/reports/usage[?format=csv]` | Usage statistics |
Login also accepts an optional `role` (the login page's role choice); a mismatch is rejected like a wrong password. Passwords must have at least 8 characters including letters and numbers.
Accepting a fix marks it for the student's own tracking and for the `?scope=accepted` export; it never edits the file by itself.

### Evaluation
Precision/recall/F1, QWK, kappa and time-saving are computed by `scripts/evaluate_*.py` from real annotations; see `docs/EVALUATION.md`. No results are bundled, and the figures in thesis Chapter 4 should be traceable to runs of these tools.

### Security model
- **CSRF:** all POST/PUT/DELETE requests need an `X-CSRF-Token` header. Call `GET /api/auth/csrf` first (needed even for register/login); login returns a fresh token. The `Origin` header is also checked.
- **CORS:** disabled unless `CORS_ORIGINS` lists your frontend origin(s). For a cross-site frontend also set `SESSION_COOKIE_SAMESITE=None` and `SESSION_COOKIE_SECURE=1`.
- **Rate limiting:** in-memory and per process (login: 5 failures per IP+email per 15 min; register: 10/h/IP; uploads: 15/h/user). Behind a reverse proxy set `TRUSTED_PROXY_COUNT`. With several worker processes use Flask-Limiter + Redis instead.
- **Uploads** are checked by content (PDF signature; DOCX structure and zip-bomb limit), not just extension. This is not malware scanning.
- The app refuses to start outside debug/testing with a placeholder `SECRET_KEY`.
- Exports are generated in memory and escape all user/LLM text.

### Background analysis
Uploads return `202` immediately and analysis runs in a thread pool (`ANALYSIS_ASYNC=1`). Poll `GET /api/student/theses/<id>/status`. The pool is in-process and not durable: jobs queued when the server restarts are lost and show as `stale`; retry them with `POST /api/student/theses/<id>/reanalyze` (allowed only for failed/stuck analyses, because re-running replaces feedback and would erase supervisor decisions). For production use Celery/RQ.

## 1. Setup (Windows PowerShell)

```powershell
cd nlp_thesis_backend
py -3.10 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env`: set a long random `SECRET_KEY`; to enable AI evaluation set `GEMINI_API_KEY` and `GEMINI_MODEL` (see notes above).

## 2. Initialize database and starter rules

```powershell
python scripts/initialize_db.py          # also upgrades databases created by older versions
python run.py
```

API health check: `http://127.0.0.1:5000/api/health`.

## 3. Initial administrator

```powershell
python scripts/create_admin.py
```

The script prompts for the admin name, email, and password. Do not expose the admin bootstrap process on a public server.

## 4. Core endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/health` | Health check |
| GET | `/api/auth/csrf` | Get the CSRF token (send as `X-CSRF-Token` on every POST/PUT) |
| POST | `/api/auth/register` | Register student |
| POST | `/api/auth/login` | Login and create session |
| POST | `/api/auth/logout` | Logout |
| GET | `/api/auth/me` | Current account |
| POST | `/api/student/upload` | Upload DOCX/PDF (`multipart/form-data`: `file`, optional `title`); returns 202 and analyses in the background |
| GET | `/api/student/theses/1/status` | Poll analysis progress (`queued`/`processing`/`analyzed`/`analysis_failed`) |
| POST | `/api/student/theses/1/reanalyze` | Retry a failed or stuck analysis |
| GET | `/api/student/theses` | List theses you may see (own; assigned students for supervisors; all for admins) |
| GET | `/api/student/reports?thesis_id=1` | List feedback |
| POST | `/api/student/resubmit` | Resubmit from the latest version (`file`, `thesis_id`) |
| GET | `/api/student/theses/1/export/pdf` | Download feedback PDF |
| GET | `/api/student/theses/1/export/docx` | Download DOCX with feedback appendix |
| GET | `/api/supervisor/dashboard` | List theses and triaged feedback |
| POST | `/api/supervisor/feedback/1` | Review feedback (`action`: accept/edit/reject/comment/reclassify) |
| GET | `/api/admin/rules` | List rules |
| PUT | `/api/admin/rules/STRUCT-001` | Update a seeded database rule |
| POST | `/api/admin/users` | Create supervisor/admin user |
| GET | `/api/admin/users?role=student` | List users |
| PUT | `/api/admin/students/1/supervisor` | Assign a supervisor (`{"supervisor_id": 2}`; null to unassign) |
| GET | `/api/admin/logs` | View audit logs |

## 5. Rules in the database
`initialize_db.py` imports new rules into the database and leaves existing rows untouched (so admin edits survive). DB rows override the JSON's description, severity, pattern and suggestion; the executable check logic always comes from the JSON. After upgrading, run `python scripts/initialize_db.py --refresh-rules` to pull the improved rule text into rows seeded by an older version (this overwrites admin edits to those fields and deactivates obsolete rules; the Chapter One/Two rules were renamed from CS-CH1/2-001 to MS-CH1/2-001).

## 6. Run tests

```powershell
pytest -q                      # everything (needs full requirements)
python -m unittest tests.test_nlp tests.test_security   # logic tests only, no database needed
```

## 7. Suggested next steps

1. Run `pytest -q` on your machine (the API tests were written without being executed in a sandbox lacking Flask-SQLAlchemy).
2. Set `GEMINI_MODEL`, run a real thesis through the AI path, and inspect the findings and `analysis_note` coverage report.
3. Test the parser and rules on a small consented set of anonymised theses; measure precision/recall/F1 per rule (Objective 5).
4. Grow `manual_sections.jsonl`, rebuild the vector store, and assess retrieval relevance.
5. Not yet implemented: malware scanning, durable job queue, real tracked changes, revision-to-revision compliance comparison, frequency-based prioritisation in the aggregator.

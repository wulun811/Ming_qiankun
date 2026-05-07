# Changelog

## [v0.11.12.post4] — 2026-05-07

### Fixed
- `web_exporter.py`: 诊断时间戳显示 bug（前端显示"3m 前"，实际事件 7.8 天前）
  - 3 层时间回退链：证据别名 → 系统事件全量历史 → created_at 兜底
  - 新增 `_normalize_ts()` 处理 timestamp 毫秒/秒单位混用
  - 新增 `FIRST_SEEN_ALIASES` / `LAST_SEEN_ALIASES` 覆盖 7 个规则的已有时间别名
  - 批量查询 events 表获取每个系统的 MIN/MAX 时间范围（全量历史，无 24h 限制）
  - `last_seen` 改用 `occurred_at_last`（取 MAX 而非 MIN）

## [v0.11.12.post1] — 2026-05-07

### Added
- `src/adapters/daemon_opencode.py` back to public release package
- `contrib/systemd/` systemd unit templates (ming-archiver, ming-web, ming-opencode)
- `updocs/` included in PyPI wheel (was missing from v0.11.12)

### Changed
- CLI docs: `ming start` → `python3 -m src.ming start` across all docs
- `updocs/06_运维手册.md`: systemd deployment section rewritten with templates
- `pyproject.toml`: packages includes `"updocs"` for wheel packaging

### Fixed
- `archiver.py`: `start_daemon()` self-registers PID in `system_pid` table (Tier 2 fallback)
- `tests/test_t01_to_t11.py`: add `src/` to `sys.path` for import resolution
- `README.md` LAN access table: old `server.py` paths → `ming web serve`
- All docs consistent: `python src/cli.py` → `ming`, version badges updated

## [v0.11.12] — 2026-05-07

### Added
- Stress test isolation: `tests/conftest.py` with `stress`/`slow` pytest markers
- `pre_build_check.py` safety scan (sync to ming-run via OVERRIDE)

### Changed
- SYS-005 FD leak threshold: `max_fd > 500` → `> 200`
- AGT-062 error cascade threshold: `cnt > 10` → `> 5`
- DQT-086 error freq threshold: `cnt > 10` → `> 5`
- MDL-036 opencode confidence: `0.2` → `0.5`
- Blocked rules: now execute with `conf * 0.3` instead of being silently skipped

### Fixed
- `lit_lite.py`: `always_on_ids` moved before closure definitions (defensive)
- `lit_lite.py`: `event_types` stored as `list` instead of `set` (defensive)
- `web_exporter.py`: archiver PID fallback to `system_pid` table

### Security
- `MANIFEST.in`: exclude `data.json` / `triage_snapshot.json`
- `pyproject.toml`: remove `*.json` wildcard (explicit file list only)
- Release guide: token must not be shared with AI agent

## [v0.11.11] — 2026-05-07 (YANKED — token leak)

### Security
- PyPI API token leaked via `llm_output` → `data.json` → tarball
- Immediate yanked; all fixes folded into v0.11.12

## [v0.11.10] — 2026-05-07

### Added
- Three-tier storage compression (zlib): hot (7d tier-0) → warm (compress tier-1) → summary (monthly tier-2)
- `archiver_compress.py`: compression/decompression + hash chain + transparent `resolve_payload()`
- `archiver_summary.py`: monthly summary aggregation + `--month` CLI report
- `scripts/compress_tools.py`: backfill/verify/downgrade utilities
- `ming upgrade`: one-click `pip install --upgrade mingjing` + auto-restart
- `_find_real_pid()`: process memory tracking for wrapper daemons (probe vs main engine)
- CLI help text + `--version` now reads from package metadata

### Fixed
- Web dashboard token statistics: all `json_extract(payload, ...)` replaced with `LEFT JOIN events_blob` + `resolve_payload()` decompression
- QueryBridge (6 methods): same pattern — `query_token_breakdown`, `query_token_spike`, `query_tool_dangerous`, `query_step_sequence`, `query_step_loop`, `query_memory_retrieve`
- `test_full_uninstall`: backup assertion synced to current behavior
- Test triage setup: added `events_blob` table + `storage_tier` column
- README badges: 403→430 tests
- Frontend version display: v0.11.9m → v0.11.10
- opencode memory tracking shows probe daemon (~21MB) + main engine (~826MB) separately

### Changed
- Base budget: 6 core files, 997 lines (< 2000 budget)
- 6 optional modules use `ImportError` pattern (delete file = disable)
- Schema auto-migration: `ALTER TABLE ... ADD COLUMN` with `try/except`

### Performance
- DB size: 267 MB → 196 MB after VACUUM (−26.7%)
- zlib compression ratio: 43.8% (100.2 MB text → 56.3 MB blob)
- Zero failures across 212,795 historical events
- Transparent decompression: zero overhead for tier-0 reads

## [v0.11.9m] — 2026-05-05

### Added
- Probe management commands (`ming probe list` / `ming probe uninstall`)
- Safe probe uninstall: backup → stop observation → DB cleanup → file cleanup → VACUUM
- `--dry-run` mode previews uninstall without executing
- `--keep-data` flag retains DB records while stopping observation
- `--force` flag skips interactive confirmation
- Protected systems guard (`__admin__`, `__self_health__`, `__host__`, `unknown`)

### Changed
- Version: 0.11.9-alpha → 0.11.9m

## [v0.11.9-alpha] — 2026-05-05

### Added
- Diagnosis timeline replay (`ming replay --system X --since 1h`)
- Environment-aware rule thresholds (container/VM/baremetal detection)
- CPU% calculation in probe_platform via `/proc/stat` jiffies delta
- `ming admin cleanup` command for stale events/diagnoses
- Healthbeat independent thread in Hermes plugin
- KNOWN_PROBES whitelist-based instance filtering (report + web dashboard)
- QueryBridge: 7 new methods + unified `/api/query` entry + 4 quick endpoints + CLI extensions
- Always-on mechanism for LIT diagnostics + 20 tusunsun diagnosis rules
- TLT-058 diagnosis enhancement + SYS-004 adaptive thresholds
- Resource footprint panel (web dashboard)
- OpenClaw probe enhancements

### Fixed
- False Podman detection on bare-metal cgroups v2
- `unknown` system fallback in diagnosis source → `mingjing`
- Archiver self-measurement inflating CPU footprint in reports
- Frontend displaying `__host__`/`unknown`/`all` as instance cards
- Probe CPU showing 0.0% for `mingjing` system-level instance
- Web dashboard export thread import caching (added importlib.reload)
- `mingjing` not rendering as a card in web dashboard
- 10 diagnosis rule false positives
- 8 diagnosis rule false positives + orphan process cleanup
- 5 diagnosis rule SQL bugs
- LangChain adapter: langchain-core >= 1.0 compatibility (invoke entry patch)
- LangChain adapter: filled 6 diagnostic gaps (session_id, cache_hit, error, agent_step, relevance, embedding)
- Dashboard diagnosis caps display: verified 3 / inferred 9

### Removed
- PRB-083 ("探针离线") and DQT-085 ("数据质量问题-探针缺失") — flawed rules assuming all probes must be online
- Triage Prism from frontend (replaced by instance card system)

### Changed
- BSL 1.1 licensing: free for orgs with annual revenue <$100K USD
- Diseases.yaml rule count: 159 → 157
- Report/frontend filtering: blacklist → KNOWN_PROBES whitelist
- Documentation restructured for open-source release
- Memory optimization across archiver and hot rail processing

## [v0.11.6] — 2026-05-01

### Added
- Health center CLI: report reorder, ignore/archive/reset commands, instance list
- Health center web: health reset / archive / ignore trinity + probe status
- Hermes Agent: `./ming hermes-install` one-click installation + skill registration
- Web: hash chain break alerts can be dismissed (fixed alert_key + dismiss API)
- Hermes probe: independent healthbeat thread, `__anchor__` support
- Hermes probe: synthetic agent_step, OS sampling, event name mapping, deep extract
- Hermes probe: tool_input_hash + 4-prong strategy
- Web: system card shows per-system diagnosis count
- CLI: report added disease summary (dedup + inference chain), `dx list` shows cause
- Package: probe directory, requirements.txt, user guide, .gitignore

### Refactored
- archiver.py 586→406 lines: split into schema/triage/score/util modules
- cli.py + lit_lite.py split for code size compliance
- CLI UX fixes: port handling, empty query, JSON consistency, time format
- P3 code style cleanup + probe dead code removal
- P2 performance/security/dead code optimization
- P2 archiver 3 tool modules split + P1 full security hardening
- P0/P1 full code audit: 28 P0 + 30 P1/P2 fixes

### Changed
- Code budget: archiver.py limit relaxed to <410 lines (atomic transaction boundary)
- 5 test case fixes + cluster_archiver hash chain bug fix

## [v0.11.5] — 2026-04-30

### Added
- OpenClaw probe: enhanced event coverage and adapter stability
- Web dashboard: alert banner dismiss support + toast deduplication
- `__health__` / `__register__` / `__touch__` synchronized write to events table

### Fixed
- SYS diagnosis rules: relaxed system filter + corrected field names
- `data.json` added to .gitignore (auto-generated runtime artifact)

## [v0.11.4] — 2026-04-30

### Added
- Full probe adapter coverage across all supported frameworks
- Configurable content truncation for large payloads
- End-to-end diagnosis pipeline: probe → archiver → diagnosis → web

## [v0.11.3] — 2026-04-29

### Added
- opencode probe: enhanced event extraction, embedding, and diagnosis gaps
- Diagnosis engine: improved rule matching and coverage

### Performance
- Backfill batch write: 36K records from 75min → 1-2min

## [v0.11.1] — 2026-04-28~29

### Added
- Self-health detection system (Phase 1+2)
- Remedy engine decoupled from diagnostics
- Configuration management (Phase 3)
- Migration system (Phase 4)
- Privacy controls (Phase 5)
- OTEL bidirectional translation hub

### Fixed
- 7 SQL schema issues in diseases.yaml (error_type → json_extract + JSON path fixes)
- 14 P0/P1 security fixes (exit mechanism, etc.)
- Code review fixes: obvious bugs + limit relaxations

### Changed
- **Project renamed: Cosmoscope → Mingjing** — env vars, paths, CLI, deployment files updated
- Full code review: module structure optimization, redundancy cleanup, style unification
- MCP → LIT naming migration across codebase
- Web Dashboard filter enhancements

## [v0.10.5] — 2026-04-29

### Added
- OTEL bidirectional translation hub implementation
- Frontend alignment: web_exporter field completion, server.py version switching, new frontend activation
- Frontend i18n missing fields + diagnosis table header translation

### Docs
- OTEL upgrade plan v1.0→v1.2 (3 revisions, expert feedback incorporated)
- Data dictionary and build specification updates

## [v0.10.3] — 2026-04-28

### Added
- Diagnosis enhancement Phase 1: 96 disease diagnosis & treatment list
- Adapter Phase 2: field completion, eliminated hardcoding, unified payload builders
- Benchmark: `bench_continuity.py` + test guide rewrite
- Smoke test + report generator + frontend Config read-only display + README tuning guide
- Archive success rate: 98% → 100%

### Docs
- Diagnosis enhancement plan v1.0→v2.3 (3 revisions, expert opinions incorporated)
- Performance benchmark report (A: 15min×1000/s, B: 3min×5000/s)
- Complete documentation supplement

## [v0.10] — 2026-04-27

### Added
- M9: Open-source preparation complete
- M10: Production environment verification passed

### Fixed
- Exit mechanism + 14 P0/P1 security issues
- mcp_received.jsonl added to .gitignore

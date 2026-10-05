# Campus Building Mapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking. This run uses inline execution within the user's mapping request and preserves the existing dirty checkout.

**Goal:** Produce a reviewable WGS84 location-to-nearby-teaching-building mapping for SWU Beibei, including buildings 8 and 25.

**Architecture:** Cache explicitly numbered OpenStreetMap building footprints with provenance; a pure Python service measures distance to footprints and includes all buildings within `max(250, accuracy + 50)` metres. A loopback preview uses the same service for simulated map clicks; no database, school login, submission, or automatic building confirmation. Public-map coordinates remain unverified on site. Rongchang and unnumbered college names are not inferred from Beibei data.

**Tech Stack:** Python standard library, pytest, standalone HTML/SVG, cached OSM XML/GeoJSON. Coordinate-system handling follows the WGS84 separation in youziauth's simulation-map-picker documentation; its simulation replay and attendance provider behaviour are not imported.

### Task 1: Source data and coverage

Files: `tools/build_campus_buildings.py`, `app/data/swu_beibei_buildings.geojson`, `output/2026-10-02-campus-building-mapping/osm-campus-raw.xml`.

- [x] Add tests for explicitly numbered university buildings, rejecting inferred college identities and retaining footprint/source URLs. Example: `assert extract_buildings(raw)['features'][0]['properties']['code'] == '8'` for a closed OSM university way named `8教学楼`.
- [x] Run `.venv-audit/Scripts/python.exe -m pytest tests/test_campus_buildings.py -q`; observe missing-feature failure.
- [x] Implement an offline importer: `ET.parse(input)`, index node WGS84 coordinates, accept closed building ways whose `name` or `alt_name` contains an explicit numeric teaching-building name; write FeatureCollection with OSM identity/version/source and `public_map_unverified` status.
- [x] Run importer on the saved OSM response and audit distinct room prefixes in the current workspace XLSX. Save mapped/unmapped prefixes separately; do not change timetable authority.

### Task 2: Pure nearby service

Files: `app/services/campus_buildings.py`, `tests/test_campus_buildings.py`.

- [x] Add behavioural tests: point inside footprint has zero distance; at building25 with accuracy35, building8 remains in the 250m neighbourhood; low accuracy increases the radius; invalid/missing/coarse fixes return no location suggestions; Rongchang does not use Beibei; `allowed_buildings=['8','46']` retains mapping8 and reports46 as unmapped. Test that a point thousands of metres away returns no suggestions.
- [x] Verify RED, then implement finite/range validation, local metric projection, point-in-polygon and segment distances; return every qualifying building sorted by footprint distance, never a hard confirmation or truncated candidate set.
- [x] Verify GREEN using the focused pytest file. Buffers are provisional and must not be described as empirically calibrated recall guarantees.

### Task 3: Local review and replay

Files: `tools/campus_mapping_preview.py`, `tools/campus_mapping_preview.html`, `docs/CAMPUS_BUILDING_MAPPING.md`, output replay JSON.

- [x] Serve only `127.0.0.1`, render saved footprints and provenance, and query the pure service at `/nearby`. Every map click and preset is labelled simulated. Provide an accuracy control and 8/25/degraded/failure examples. Use no external tiles, user account, or original database.
- [x] Compare preset results to Python replay JSON, verify the preview in a real browser at desktop and mobile widths, and preserve a screenshot.
- [x] Document source date, campus scope, all source links, distance method, provisional neighbourhood radius, missing building coverage, and integration contract. Record focused test results; leave production capture/UI unchanged.

## Execution evidence

17 focused pytest cases passed. Saved 14 explicitly numbered OSM footprints. Compared 34 numeric non-Rongchang workbook prefixes: 11 mapped, 23 still unmapped. Browser confirmed 8/25/degraded/failed simulations, saved desktop/mobile screenshots, and found no horizontal overflow at390px. Production capture and existing databases remain unchanged. No commit or external publication requested/performed.

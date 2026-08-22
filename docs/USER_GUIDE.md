# User Guide

## Input folders

- input/song: primary file used by single, codec, streaming, repair, and
  complete analysis
- input/reference: comparison target
- input/batch: recursively analyzed folder material
- input/album: two or more tracks used for album consistency

If song or reference contains multiple files, the largest supported audio file
is selected. Explicit CLI paths always take precedence.

## Workflows

Single-file analysis measures the source, scores technical health, evaluates
streaming platforms and source codec suitability, and writes three report
formats.

Reference comparison measures both files, reports numeric differences, and
creates a conservative reference-match command. It does not claim to perform
automatic mastering.

Batch analysis recursively scores every supported file and writes a CSV and
dashboard. Album analysis additionally measures each track against album
medians for loudness, true peak, and crest-based dynamics.

Codec analysis produces actual MP3, AAC, and Opus preview encodes when the
encoders are available. Streaming analysis produces actual platform-normalized
AAC previews. Use --no-previews when only readiness calculations are wanted.

Repair analysis writes a report and a BAT command under exports/repairs. The
source is never modified or deleted.

Complete analysis runs every applicable workflow. Missing optional reference,
batch, or album input is explicitly recorded as skipped. Required song-based
operations fail clearly when no song exists.

## Output folders

- reports/html: interactive dashboards
- reports/txt: plain-language engineering reports
- reports/json: machine-readable records
- reports/csv: batch and album summaries
- reports/history: append-only analysis index
- exports/previews: rendered codec and streaming comparisons
- exports/repairs: repair commands and rendered repair targets
- exports: report ZIP bundles
- logs: rotating diagnostic logs


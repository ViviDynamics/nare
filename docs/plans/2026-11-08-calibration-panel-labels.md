# Calibration: record model-panel labels separately from human labels, and report rates against each

Issue #264

## Scope
In: a `panel` entry on a labels row, holding the panel's verdict and its provenance
(the passes run, each with its date and its models and their effort, and optionally the
vote split per pass); validation that keeps a panel verdict out of the human slots;
`calibrate` and the report giving the judge's rates against human-settled rows and
against panel rows as separate lines with their counts, plus how often a human verdict
and the panel agree; `calibrate import` reading a CSV `panel` column or a JSON
`reviewer: "panel"` entry with a `--panel-provenance` file; one human verdict plus a
panel verdict on the same claim; docs.
Out: running the panel. How labels are produced stays outside nare.

## Assumptions
- Human labels keep their two slots, `a` and `b`, and their rules. A row is
  human-settled only when both humans answered and agree, or an adjudicator decided.
  `status`, `label` and `adjudicated` keep meaning exactly that.
- A panel verdict lives only in the row's `panel` entry. It is never in `labels`,
  never in `label`, and never changes `status`. A file with no `panel` entries loads,
  writes and reports byte for byte as before.
- One human verdict on a row is not a human label. The row's `status` stays `pending`
  and it is out of the human rates, as before. It is still useful: the row is scored
  against its panel label in the panel rates, and the single human verdict is counted
  in the human/panel agreement line. A second human verdict later settles the row as
  before.
- Agreement is counted per human verdict, not per row: each true or false from `a` or
  `b` on a row whose panel label is true or false is one comparison. An `unknown` from
  either side is no comparison.
- A panel label of `null` is the panel's `unknown`: kept, and in no rate.
- A reviewer name of `panel`, or one that is a model named in the row's panel
  provenance, is refused as a human reviewer, in the labels file and at import.
- At import, a JSON entry whose reviewer is a model named in the provenance is refused:
  a single model's verdict is not a human one, and the panel's is given as `panel`.
- Provenance comes from one `--panel-provenance` JSON file per import, so every panel
  verdict in one import shares it. Claims with a different panel history are imported
  in a separate file. The vote split is per claim, from the JSON entry's `votes`.
- `--reviewer-a` and `--reviewer-b` are needed only when the file has verdicts for
  that labeller, so a panel-only or one-human file imports without a made-up name.

## Tasks
- [x] 1. Labels format: a `panel` entry loads and round-trips; a bad one is refused;
  a panel model or `panel` as a human reviewer is refused.
- [x] 2. Scoring: rates against panel rows, apart from human rows, and the agreement
  count; a single human verdict plus a panel verdict scores in the panel rates only.
- [x] 3. Report: separate human and panel lines with counts, and the agreement line;
  a human-only file renders as before.
- [x] 4. Import: CSV `panel` column and JSON `panel` reviewer, with provenance; refusals
  for missing provenance, a model or `panel` as a human, conflicting panel verdicts.
- [x] 5. CLI: `--panel-provenance`, reviewer names only as needed, `check` and the
  import summary count panel labels.
- [ ] 6. Docs: benchmarks README and the module docstrings say what a panel label is
  and is not.

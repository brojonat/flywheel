# TODO

## Up next

- [x] Support SQLite and Parquet ingest alongside CSV in `scripts/generate.sh` — detect file extension, use `sqlite-utils insert` for CSV/SQLite and DuckDB (or pyarrow) for Parquet
- [x] Bug: large integer IDs rendered as scientific notation in the frontend — causes submission failures when the form posts e.g. `1.23e+10` instead of the actual integer
- [x] Hierarchical label UI: stack `.hier-group` entries vertically instead of inline/horizontal
- [x] Modernize input styling across all field kinds (dropdowns, checkboxes, chips, textareas)

## Labeling (leg 1 — implemented)

- [ ] More examples — additional `examples/<slug>/` exercises beyond `vehicle_safety` and `nhtsa_complaints`

## Train (leg 2 — not started)

- [ ] Fine-tune a small local model (Gemma 4) on exported gold JSONL
- [ ] Single multi-target instruct model emitting all label fields as one JSON object per inference
- [ ] Integration with the export notebook output

## Evaluate (leg 3 — not started)

- [ ] Run fine-tuned model + zero-shot baseline across full dataset
- [ ] Score against ground truth
- [ ] Surface where the model is weakest (per-field, per-class error rates)

## Relabel (leg 4 — not started)

- [ ] Feed low-confidence / high-error records back into the labeling queue
- [ ] Prioritize examples that most improve the next training run (active learning)

## Deploy (leg 5 — not started)

- [ ] Package the full loop as a service: YAML in, labeled dataset + trained model out
- [ ] Continuous relabeling cycle

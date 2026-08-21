# Face matching: threshold + ambiguity margin

Recognition marks a student present when their face embedding is close enough to
an enrolled template. Two knobs decide "close enough", both on cosine similarity
of L2-normalized ArcFace embeddings (range 0..1), both settable in
`face-service/.env`:

| Env | Meaning | Default |
|-----|---------|---------|
| `MATCH_THRESHOLD` | Minimum cosine similarity to the best-matching enrolled angle. | `0.35` |
| `MATCH_MARGIN` | The best identity must beat the **runner-up (a different student)** by at least this much, else the match is rejected as `ambiguous`. `0` disables. | `0.05` |

## Why a margin, not just a threshold

A single threshold answers "is this close to *someone*?" but not "is it clearly
*this* someone and not their look-alike?". When two different students both clear
the threshold and score within a hair of each other, marking the top one is a
coin flip. The margin guard rejects that near-tie instead — the recognizer
returns `recognized=false, reason="ambiguous"`, the overlay shows *Unknown*, and
nobody is auto-marked. We would rather miss a mark (the student can self-check-in
or be marked manually, and disputes exist) than mark the wrong person.

Extra enrolled **angles of the same student never count** as the runner-up, so
multi-angle enrollment does not trip the guard — the runner-up is always the best
score from a *different* `sid`.

## Calibrating both on your own data

`eval/benchmark.py` measures FAR/FRR across thresholds, the EER point, rank-1
accuracy, **and** the genuine-margin distribution, then prints a recommended
`MATCH_THRESHOLD` and `MATCH_MARGIN`.

```bash
cd face-service
# real photos: one subfolder per person (see eval/README.md)
.venv/bin/python -m eval.benchmark --data /path/to/dataset --far-target 0.01

# no photos yet? validate the math on synthetic identities
.venv/bin/python -m eval.benchmark --synthetic 40
```

Read the two `RECOMMENDED` lines at the end and copy them into `.env`:

```
MATCH_THRESHOLD=0.xx
MATCH_MARGIN=0.xx
```

- **Threshold** is chosen as the strictest value whose FAR is within
  `--far-target` (attendance should bias toward low false-accepts). Falls back to
  the EER threshold if none reaches the target.
- **Margin** is set at the `--margin-percentile` (default 1) of genuine margins,
  so roughly that percentage of real matches fall to the guard while genuine
  near-confusions below it are caught. Raise the percentile for a stricter guard,
  lower (or `0`) to loosen it.

`/api/health` reports the live `match_threshold` and `match_margin` so an operator
can confirm what the server is actually enforcing.

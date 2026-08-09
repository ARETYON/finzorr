"""Runtime citation-marker validation.

The exact `[(\\d+)]` + range-check logic proven in `evals/grounded_eval.py`
(`_eval_web`/`_eval_research`), extracted so it runs at RUNTIME too — not
just as a manual eval. Observe-only: never mangles model output (mangling
risks breaking a correct answer over a false positive); callers tag/log on
a non-empty result.
"""

import re

_MARKER = re.compile(r"\[(\d+)\]")


def find_invalid_markers(text: str, citation_count: int) -> list[int]:
    """`[n]` markers in `text` whose `n` falls outside the retrieved
    citations' range `[1, citation_count]` — i.e. the model invented a
    source that was never actually retrieved."""
    markers = {int(m) for m in _MARKER.findall(text)}
    valid = set(range(1, citation_count + 1))
    return sorted(markers - valid)

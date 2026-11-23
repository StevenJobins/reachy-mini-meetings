"""How often the label on what someone says is right, per situation: simulated meetings with real voices.

    python scripts/eval_scenarios.py                 # the table (tests/sim.py says what is real and what is simulated)
    python scripts/eval_scenarios.py --seeds 20
    python scripts/eval_scenarios.py --voices x.npz  # another voice model's embeddings (eval_voices.py fixture --out)

The same meetings run as tests in tests/test_scenarios.py, with floors under these numbers.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

import sim


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--voices", default=str(sim.FIXTURE))
    ap.add_argument("--only", help="scenarios whose name contains this")
    ap.add_argument("-v", "--verbose", action="store_true", help="every seed")
    args = ap.parse_args()
    voices = sim.Voices.load(Path(args.voices))
    pairs = voices.similar_pairs()
    print(f"voices: {voices.speakers()} speakers; most similar pair {pairs[0][0]:.2f}, an ordinary pair "
          f"{pairs[len(pairs) // 2][0]:.2f}; {args.seeds} meetings of 60 utterances each\n")
    total = []
    for scn in sim.scenarios(voices) + sim.field_scenarios(voices):
        if args.only and args.only not in scn.name:
            continue
        results = [sim.run(scn, voices, seed) for seed in range(args.seeds)]
        if args.verbose:
            for r in results:
                print(f"    {r}")
        m = sim.mean(results)
        total.append(m)
        print(f"{scn.name:62s} {m}  (of {len(scn.seats)})")
    print(f"\n{'all':62s} {sim.mean(total)}")


if __name__ == "__main__":
    main()

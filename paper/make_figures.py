#!/usr/bin/env python3
"""Generate the manuscript's figures from the released results.

Every value plotted is recomputed here from `results/*.jsonl`, never typed in,
so a figure cannot drift from the table it illustrates the way prose can. The
audit script re-derives the same quantities independently and compares them
against the LaTeX source; if a figure and a table ever disagree, one of the two
scripts is wrong and the build should be treated as broken.

Output is vector PDF at the manuscript's text width. Nothing here uses colour to
mean pass or fail: the study's position is that an artifact which does not run is
not thereby wrong, and a red-to-green ramp would contradict that before a reader
has read a word.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch  # noqa: E402
from matplotlib.transforms import blended_transform_factory, offset_copy  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "paper" / "figures"

#: Single hue ramp, pale where every artifact clears to saturated where almost
#: none does. Depth on the ladder, not a verdict on the paper.
RAMP = ["#D9D2C6", "#BFC4B8", "#9FB4AB", "#7CA39E", "#589391", "#3C7C7A", "#2A6664", "#1F5451"]
INK = "#1B1E22"
SOFT = "#565C63"
FAINT = "#8A9098"
RULE = "#DCD6CC"

RUNGS = [
    (2, "URL present"),
    (3, "URL resolves"),
    (4, "Clones"),
    (5, "Declares dependencies"),
    (6, "Environment builds"),
    (7, "Entry point found"),
    (8, "Runs"),
    (9, "Produces output"),
]


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def cleared(records: list[dict], rung: int) -> int:
    return sum(1 for r in records if r["highest_rung"] >= rung)


def figure_attrition(primary: list[dict], conservative: list[dict]) -> None:
    n = len(primary)
    counts = [cleared(primary, r) for r, _ in RUNGS]
    alt = [cleared(conservative, r) for r, _ in RUNGS]

    fig, ax = plt.subplots(figsize=(6.5, 4.0))
    ys = list(range(len(RUNGS)))[::-1]
    # x in axes fraction (1.0 = the plot's right edge), y in data units.
    edge = blended_transform_factory(ax.transAxes, ax.transData)

    def label_at(points: float):
        return offset_copy(edge, fig=fig, x=points, units="points")

    def shift(points: float):
        # Translate an (x=1.0 axes, y=data) anchor right by a fixed number of
        # points; used for the per-row labels and the drop annotations alike.
        return offset_copy(edge, fig=fig, x=points, units="points")

    for i, ((rung, label), y) in enumerate(zip(RUNGS, ys)):
        pct = 100 * counts[i] / n
        ax.barh(y, pct, height=0.62, color=RAMP[i], zorder=3)
        # The conservative specification is drawn as an outline inside the bar
        # rather than as a second bar: it is the same measurement under a
        # stricter entry-point rule, not a separate population.
        if alt[i] != counts[i]:
            ax.barh(y, 100 * alt[i] / n, height=0.62, facecolor="none",
                    edgecolor=INK, linewidth=0.9, linestyle=(0, (2.5, 1.5)), zorder=4)
        # The label column sits just past the plot's right edge and is offset in
        # points, not data units. Two earlier versions placed it in data units,
        # and the count and percentage overprinted each other whenever the axis
        # width changed: text width is fixed in points, a data unit is not.
        ax.text(1.0, y, f"{counts[i]:>3d}", transform=label_at(6), va="center",
                ha="left", fontsize=8.5, color=INK, family="monospace",
                clip_on=False, zorder=5)
        ax.text(1.0, y, f"{pct:5.1f}%", transform=label_at(30), va="center",
                ha="left", fontsize=8, color=FAINT, family="monospace",
                clip_on=False, zorder=5)

    ax.set_yticks(ys)
    ax.set_yticklabels([f"{r}  {lab}" for r, lab in RUNGS], fontsize=9, color=SOFT)
    ax.set_xlim(0, 100)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_xticklabels(["0", "25", "50", "75", "100%"], fontsize=8, color=FAINT)
    ax.tick_params(length=0)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(RULE)
    ax.grid(axis="x", color=RULE, linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)

    # The two annotated drops are the paper's finding: the loss is not at link
    # rot, which is where the discourse points, but at declaring and building an
    # environment.
    decl_drop = counts[2] - counts[3]
    build_drop = counts[3] - counts[4]
    for y_from, y_to, text in (
        (ys[2], ys[3], f"{decl_drop} ship no\ndependency spec"),
        (ys[3], ys[4], f"{build_drop} declare them\nbut cannot install"),
    ):
        ymid = (y_from + y_to) / 2
        ax.add_patch(FancyArrowPatch(
            (1.0, y_from - 0.18), (1.0, y_to + 0.18), transform=shift(76),
            arrowstyle="-|>", mutation_scale=7, linewidth=0.8, color=SOFT,
            clip_on=False, zorder=6))
        ax.text(1.0, ymid, text, transform=shift(82), va="center", ha="left",
                fontsize=7.4, color=SOFT, linespacing=1.35, clip_on=False, zorder=6)

    ax.set_xlabel(f"share of the {n} papers advertising a repository", fontsize=8.5,
                  color=SOFT, labelpad=7)
    fig.tight_layout(pad=0.6)
    path = OUT / "attrition.pdf"
    fig.savefig(path, bbox_inches="tight", transparent=True)
    plt.close(fig)
    print(f"wrote {path.relative_to(ROOT)}  (counts {counts})")


def figure_reasons(primary: list[dict]) -> None:
    """Why artifacts stop, grouped by the rung that blocked them."""
    groups: dict[str, list[tuple[str, int]]] = {}
    for r in primary:
        if not r.get("blocked_at"):
            continue
        groups.setdefault(r["blocked_at"], [])
    tally: dict[tuple[str, str], int] = {}
    for r in primary:
        if r.get("blocked_at"):
            key = (r["blocked_at"], r["block_reason"])
            tally[key] = tally.get(key, 0) + 1

    order = [lab for _, lab in RUNGS][1:]
    rung_of = {lab: i for i, (_, lab) in enumerate(RUNGS)}
    name_of = {
        "URL_RESOLVES": "URL resolves", "CLONED": "Clones",
        "DEPS_DECLARED": "Declares dependencies", "ENV_BUILDS": "Environment builds",
        "ENTRYPOINT_FOUND": "Entry point found", "RUNS": "Runs",
        "PRODUCES_OUTPUT": "Produces output",
    }
    rows = []
    for (blocked, reason), count in tally.items():
        rows.append((name_of.get(blocked, blocked), reason, count))
    rows.sort(key=lambda t: (order.index(t[0]) if t[0] in order else 99, -t[2]))

    # Each blocking rung gets a heading row of its own. An earlier version set
    # the rung names as floating text at the right margin, where the longest bar
    # (180, no dependency spec) ran straight through two of them.
    entries = []  # (label, value or None for a heading, colour, is_heading)
    prev = None
    for stage, reason, count in rows:
        if stage != prev:
            entries.append((f"stopped at: {stage}", None, None, True))
            prev = stage
        entries.append((reason.replace("_", " "), count,
                        RAMP[rung_of.get(stage, 4)], False))

    fig, ax = plt.subplots(figsize=(6.5, 6.4))
    ys = list(range(len(entries)))[::-1]
    values = [e[1] for e in entries if e[1] is not None]
    for y, (label, value, colour, heading) in zip(ys, entries):
        if heading:
            continue
        ax.barh(y, value, height=0.68, color=colour, zorder=3)
        ax.text(value + 2.5, y, str(value), va="center", ha="left",
                fontsize=8, color=INK)
    ax.set_yticks(ys)
    ax.set_yticklabels([e[0] for e in entries])
    for tick, (_, _, _, heading) in zip(ax.get_yticklabels(), entries):
        if heading:
            tick.set_fontsize(7.6)
            tick.set_fontstyle("italic")
            tick.set_color(FAINT)
        else:
            tick.set_fontsize(8.2)
            tick.set_family("monospace")
            tick.set_color(SOFT)
    ax.set_xlim(0, max(values) * 1.12)
    ax.tick_params(length=0)
    ax.tick_params(axis="x", labelsize=8, labelcolor=FAINT)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(RULE)
    ax.grid(axis="x", color=RULE, linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)
    ax.set_xlabel("repositories stopping for this reason", fontsize=8.5,
                  color=SOFT, labelpad=7)

    fig.tight_layout(pad=0.6)
    path = OUT / "reasons.pdf"
    fig.savefig(path, bbox_inches="tight", transparent=True)
    plt.close(fig)
    print(f"wrote {path.relative_to(ROOT)}  ({len(rows)} reasons, total {sum(values)})")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    primary = load(ROOT / "results" / "ladder_v5.jsonl")
    conservative = load(ROOT / "results" / "ladder_v5_conservative.jsonl")
    figure_attrition(primary, conservative)
    figure_reasons(primary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
The evaluation set.

Grouped by the retrieval challenge each class poses, not by topic. The
groups are the independent variable that makes the results interpretable:
the literature predicts metadata gains are uneven across question type
(see the decomposition study in section 2.6 of the review), so reporting a
single averaged score across all 40 would hide the actual finding.

Edit freely — run_eval.py and the rating UI both read this list.
Every question needs a unique id.
"""

GROUPS = {
    "A": dict(name="Factual / definitional",
              hypothesis="Baseline handles these. Metadata gives at most a "
                         "modest edge; a null result here is informative."),
    "B": dict(name="Structural / hierarchical",
              hypothesis="Tree-path metadata should give a clear edge: the "
                         "answer depends on where content sits, not just what it says."),
    "C": dict(name="Synthesis",
              hypothesis="Largest expected edge. Answers span many fragments; "
                         "weight signals which are load-bearing."),
    "D": dict(name="Contrastive",
              hypothesis="Type/section metadata should stop the system "
                         "conflating the author's own doctrine with comparative notes."),
    "E": dict(name="Inferential",
              hypothesis="Hardest for baseline. Depends on surfacing the right "
                         "premises at high weight rather than any single passage."),
    "F": dict(name="Implicit / distributed",
              hypothesis="No single passage contains the answer. Tests recall "
                         "across scattered fragments."),
    "X": dict(name="Stress / edge",
              hypothesis="Included to characterise how each system behaves at "
                         "the edge of the corpus, not to score them. Answering "
                         "beyond what the fragments say is not automatically "
                         "wrong here — what matters is whether the system is "
                         "clear about which parts are the archive's."),
}

QUESTIONS = [
    # ── A: factual / definitional ─────────────────────────────────────
    dict(id="A1", group="A", q="What is Aetherialism?"),
    dict(id="A2", group="A", q="Define the Elevation of Life as used in Aetherian doctrine."),
    dict(id="A3", group="A", q="What is the Ecclesia Militans?"),
    dict(id="A4", group="A", q="What is the Cultus Civium Aeternum?"),
    dict(id="A5", group="A", q="What is the Aetherian concept of Heaven?"),
    dict(id="A6", group="A", q="What is Aetheriocracy?"),

    # ── B: structural / hierarchical ──────────────────────────────────
    dict(id="B1", group="B", q="How is the belief system architecturally organized? "
                               "What are its main layers or domains?"),
    dict(id="B2", group="B", q="What is the relationship between Eternal Law, Natural Law, "
                               "Human Law, and Aetherian Law?"),
    dict(id="B3", group="B", q="What is the hierarchy of authority in the governance system?"),
    dict(id="B4", group="B", q="How does the Scalae Naturae (chain of being) connect to "
                               "the system of governance?"),
    dict(id="B5", group="B", q="What is the relationship between the belief system and "
                               "the State in Aetheriocracy?"),
    dict(id="B6", group="B", q="How do the 10 levels of integration into the system relate "
                               "to the nobility structure?"),

    # ── C: synthesis ──────────────────────────────────────────────────
    dict(id="C1", group="C", q="What are the core philosophical influences on Aetherialism, "
                               "and how are they synthesized?"),
    dict(id="C2", group="C", q="How does Aetherialism justify hierarchy — philosophically, "
                               "spiritually, and institutionally?"),
    dict(id="C3", group="C", q="What does Aetherialism consider the highest expression of "
                               "human potential, and how is it developed?"),
    dict(id="C4", group="C", q="How does the concept of struggle function across the "
                               "metaphysical, personal, and civilizational levels?"),
    dict(id="C5", group="C", q="How does Aetherialism resolve the tension between individual "
                               "elevation and subordination to the community?"),
    dict(id="C6", group="C", q="How does the theory of history inform its civilizational goals?"),
    # C7 dropped — trimmed to keep the rating workload manageable; C1-C6
    # already gives this group (largest expected metadata edge) enough signal.
    # dict(id="C7", group="C", q="What is the theory of legitimate authority, and how is it derived?"),

    # ── D: contrastive ────────────────────────────────────────────────
    dict(id="D1", group="D", q="How does the Aetherian concept of freedom differ from other "
                               "conceptions of freedom?"),
    dict(id="D2", group="D", q="How does the Aetherian view of natural law compare to the "
                               "Thomistic view?"),
    dict(id="D3", group="D", q="How does the view of struggle compare to Nietzsche's "
                               "will to power?"),

    # ── E: inferential ────────────────────────────────────────────────
    dict(id="E1", group="E", q="According to Aetherian Law, what makes a human law invalid "
                               "or illegitimate?"),
    dict(id="E2", group="E", q="What would this system consider the greatest threat to "
                               "civilization, and why?"),
    dict(id="E3", group="E", q="Based on the doctrine, how should a community respond to a "
                               "member who consistently avoids struggle and seeks comfort?"),

    # ── F: implicit / distributed ─────────────────────────────────────
    dict(id="F1", group="F", q="What are the Four Laws of Life, and how does each manifest at "
                               "the individual, community, and civilizational level?"),
    # F2, F4 dropped — F1/F3/F5 already cover distributed recall and
    # unfinished-doctrine detection between them.
    # dict(id="F2", group="F", q="Reconstruct the theology of the Divine: what is Heaven, how "
    #                            "does it relate to natural law, and what does it demand of "
    #                            "human beings?"),
    dict(id="F3", group="F", q="What is the intellectual spine or backbone from which this "
                               "system is derived?"),
    # dict(id="F4", group="F", q="Explain the doctrine of Ordered Sovereignty as far as the "
    #                            "archive develops it, and say what remains unfinished."),
    dict(id="F5", group="F", q="Which doctrines does the archive name but leave only "
                               "partially worked out?"),

    # ── X: stress / failure-mode ──────────────────────────────────────
    # X1, X3 dropped — X2/X4 are the two methodologically sharpest edge
    # cases (out-of-corpus handling; baseline structurally cannot see the
    # answer at all), per this group's own hypothesis.
    # dict(id="X1", group="X", q="Describe the aesthetic philosophy and its connection "
    #                            "to doctrine."),
    dict(id="X2", group="X", q="What does the archive say about quantum computing?",
         note="Out of corpus. Watch whether the system says so, and whether "
              "metadata changes how gracefully it handles the gap."),
    # dict(id="X3", group="X", q="Summarise the archive's position on the Doctrine of "
    #                            "Reciprocal Ascension.",
    #      note="Invented doctrine name. Watch whether the system notices it is "
    #           "not in the archive, or quietly invents a position for it."),
    # X4 dropped — its premise assumed orange consistently marks "needs
    # revision" across the archive, but that mapping was never applied
    # consistently, so the question doesn't isolate what it was meant to.
    # dict(id="X4", group="X", q="Which parts of the doctrine are marked as needing revision?",
    #      note="Answerable only from orange (corrective) highlights. The "
    #           "baseline cannot see this metadata at all."),
]

BY_ID = {q["id"]: q for q in QUESTIONS}


def in_group(g: str):
    return [q for q in QUESTIONS if q["group"] == g.upper()]


if __name__ == "__main__":
    for g, meta in GROUPS.items():
        qs = in_group(g)
        print(f"\n{g} — {meta['name']}  ({len(qs)} questions)")
        print(f"   {meta['hypothesis']}")
        for q in qs:
            print(f"   {q['id']}  {q['q']}")
    print(f"\nTotal: {len(QUESTIONS)} questions")

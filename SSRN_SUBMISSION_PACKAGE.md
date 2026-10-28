# SSRN submission package  Paper 7

Everything below is paste-ready. Regenerate the PDF with `bash paper/build.sh`
and confirm `python3 paper/audit.py` prints `ALL CHECKS PASSED` before uploading.

The abstract below is generated from `paper/main.tex` and is character-identical
to the manuscript. Do not hand-edit it here; edit the manuscript and regenerate,
or the public record and the PDF will disagree.

---

## PDF to upload

```
papers/paper7-execution-repro/paper/submission-ssrn/Khan_2026_execution_repro_v0.1.pdf
```

## Content type

Preprint / Working Paper (WPS)

## Title

```
Does It Actually Run? An Execution-Based Reproducibility Study of Published Machine-Learning Finance Research
```

## Author

Akram Khan, Independent Researcher, ORCID 0009-0002-7521-8648, 1819ak@gmail.com

## Date written

October 2026

## Abstract

Reproducibility in empirical finance is normally measured by what a paper says: whether it claims code is available, and whether a link appears. That is a property of the manuscript, not of the research. This paper measures the property that matters to anyone who wants to build on published work, by taking the released artifacts and attempting to run them.

We introduce the Reproducibility Attrition Ladder, an ordered scale of eleven cumulative rungs running from "the paper makes no code-availability claim" to "an emitted number matches the paper's headline result". Every rung except the last is decided mechanically, and every failure is recorded against a closed vocabulary of reasons, so the point at which an artifact stops, and the reason it stops, are both reportable as data rather than as anecdote. We apply the ladder to a corpus of arXiv quantitative-finance machine-learning papers, cloning each advertised repository, building its declared environment inside a container, and executing its entry point under a fixed time budget.

Applied to 459 arXiv quantitative-finance machine-learning papers that advertise a code repository, the curve falls steeply and not where the reproducibility discourse usually points. Link rot removes 4.4%. The two large losses are environment declaration, where 180 of 432 cloned repositories ship no dependency specification at all, and environment construction, where 110 of the 252 that declare dependencies can no longer install them, through packages that have left the index, pin sets that no longer resolve, and sources that no longer compile. Fifteen point five percent yield an identifiable entry point and seven papers, 1.5%, execute to completion and emit output without correspondence, manual repair, or reading beyond the README. Adjudicating those seven by hand against the papers' own claims leaves one that regenerated a published table, verified cell by cell; the rest emitted a self-test, an error, or an analysis run on data that never arrived. That single match depends on the looser of our two entry-point rules and on stored model outputs committed to the repository, and under the stricter rule the adjudicated count is zero rather than one, so the top of the ladder is reported as a bound and not as a rate. The earlier and smaller sample against which these figures might have been checked is nested inside this one and was scored by an earlier version of the harness, so it supports no claim of generality; on the 305 papers added since, the two large drops differ by 1.5 and 4.5 percentage points, and we show that even that agreement is partly an artifact of differing age composition.

The contrast with the existing finance result is the point. Running the code of 168 coordinated research teams on supplied raw data reproduces the published answer 52% of the time (Pérignon et al., 2024); starting instead from the URL a published paper gives its readers, with no data supplied and no author contact, 2.8% of artifacts run, and 1.5% emit output. Most of what computational reproducibility measures in finance depends on conditions an ordinary reader does not have.

The finding is about artifacts rather than findings: released code that does not run unassisted is not thereby wrong, but it cannot be checked without reimplementation. Artifacts from earlier cohorts build markedly less often than recent ones, but a single cross-section cannot separate aging from improving practice, and we report the gradient without claiming the mechanism.

The measurement instrument is itself released as an installable package, so the study can be repeated on other corpora, re-run as the literature changes, and extended to fields beyond finance. We are explicit about what the ladder does and does not establish: an artifact that fails to run is not thereby wrong, it is not independently executable as released, which is a weaker but more precisely measurable claim.

## Keywords

Paste into the Keywords field only. JEL codes go in the JEL field, not here.

```
reproducibility, computational reproducibility, research artifacts, machine learning, quantitative finance, meta-science, open science, research software, artifact evaluation, replication
```

## JEL classification

```
C63, C88, G17
```

## Companion code and data

```
https://github.com/ayk5511/exec-repro
```

The harness, the corpus manifest, the full per-paper outcome records for both
specifications, and the rung-10 adjudication worksheet.

## SSRN classifications

Pick up to seven. This paper is about research practice and software, not about
an asset class, so the volatility, derivatives and capital-markets alert journals
are the wrong home for it.

1. Econometric and Statistical Methods
2. Information Systems and Economics
3. Research Methods and Methodology
4. Integrity Practices
5. Banking and Financial Institutions

## Notes for the submission form

- Sole author. No conflicts to declare. Not under consideration elsewhere.
- The manuscript does NOT name any of the repositories it evaluated. Failure
  modes are reported in aggregate and by reason code. The one artefact scored as
  a match is described by its entry-point filename only. Per-repository outcomes
  are in the released data, which is necessary for the result to be checkable and
  is standard in artefact evaluation.
- Failures are framed throughout as "not independently executable as released",
  never as the work being wrong.

## After distribution

1. Record the abstract ID in `papers/_claims.yaml`.
2. Cross-list on arXiv (cs.SE primary, q-fin.CP cross-list). This paper's
   audience is meta-science and research-software readers, who are on arXiv
   rather than SSRN.
3. Consider publishing `exec-repro` to PyPI. The name is unclaimed.

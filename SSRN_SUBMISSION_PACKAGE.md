# SSRN submission package — Paper 7

Everything below is paste-ready. Regenerate the PDF with `bash paper/build.sh`
and confirm `python3 paper/audit.py` prints `ALL CHECKS PASSED` before uploading.

---

## PDF to upload

```
papers/paper7-execution-repro/paper/submission-ssrn/Khan_2026_execution_repro_v0.1.pdf
```

19 pages.

## Content type

Preprint / Working Paper (WPS)

## Title

```
Does It Actually Run? An Execution-Based Reproducibility Study of Published Machine-Learning Finance Research
```

## Author

Akram Khan · Independent Researcher · ORCID 0009-0002-7521-8648 · 1819ak@gmail.com

## Date written

August 2026

## Abstract

Reproducibility in empirical finance is normally measured by what a paper says: whether it claims code is available, and whether a link appears. That is a property of the manuscript, not of the research. This paper measures the property that matters to anyone who wants to build on published work, by taking the released artefacts and attempting to run them.

We introduce the Reproducibility Attrition Ladder, an ordered scale of eleven cumulative rungs running from "the paper makes no code-availability claim" to "an emitted number matches the paper's headline result". Every rung except the last is decided mechanically, and every failure is recorded against a closed vocabulary of reasons, so the point at which an artefact stops, and the reason it stops, are both reportable as data rather than as anecdote. We apply the ladder to a corpus of arXiv quantitative-finance machine-learning papers, cloning each advertised repository, building its declared environment inside a container, and executing its entry point under a fixed time budget.

Applied to 459 arXiv quantitative-finance machine-learning papers that advertise a code repository, the curve falls steeply and not where the reproducibility discourse usually points. Link rot removes 5.2%. The two large losses are environment declaration, where 176 of 427 cloned repositories ship no dependency specification at all, and environment construction, where half of those that do declare dependencies can no longer install them, through packages that have left the index, pin sets that no longer resolve, and sources that no longer compile. Thirteen point nine per cent yield an identifiable entry point and seven papers, 1.5%, execute to completion and emit output without correspondence, manual repair, or reading beyond the README. Adjudicating those seven by hand against the papers' own claims leaves one that regenerated a published table, verified cell by cell; the rest emitted a self-test, an error, or an analysis run on data that never arrived. The most discretionary step, deciding what to run, is reported under two specifications that agree exactly on every rung decided before it applies, and the mechanically determined rungs replicate on an earlier, independently drawn sample.

The contrast with the existing finance result is the point. Running the code of 168 coordinated research teams on supplied raw data reproduces the published answer 52% of the time (Pérignon et al., 2024); starting instead from the URL a published paper gives its readers, with no data supplied and no author contact, 2% of artefacts run, and 1.5% emit output. Most of what computational reproducibility measures in finance depends on conditions an ordinary reader does not have.

The finding is about artefacts rather than findings: released code that does not run unassisted is not thereby wrong, but it cannot be checked without reimplementation. Because the dominant environment failure is a dependency leaving the index rather than an omission at submission, executability decays after publication, and the same corpus will score lower each year.

The measurement instrument is itself released as an installable package, so the study can be repeated on other corpora, re-run as the literature changes, and extended to fields beyond finance. We are explicit about what the ladder does and does not establish: an artefact that fails to run is not thereby wrong, it is not independently executable as released, which is a weaker but more precisely measurable claim.

## Keywords

```
reproducibility, computational reproducibility, research artefacts, machine learning, quantitative finance, meta-science, open science, research software, artifact evaluation, replication
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

## Suggested SSRN classifications

Pick up to seven. In priority order:

1. **Econometric & Statistical Methods** — the instrument and its scoring
2. **Information Systems & Economics** — the released software
3. **Econometric Modeling: Capital Markets** — the corpus domain
4. **Research Methods & Methodology** — the meta-science contribution
5. **Banking & Financial Institutions** — the ML-finance audience
6. **Risk Management** — overlaps the model-risk readership of Papers 4 and 6

Avoid the derivatives, volatility and capital-markets alert journals: this paper
is about research practice, not about an asset class.

## Notes for the submission form

- No conflicts to declare; sole author; not under consideration elsewhere.
- The paper names specific repositories and their failure modes. This is
  necessary for the result to be checkable and is standard in artefact
  evaluation. Failures are framed throughout as "not independently executable as
  released", never as the work being wrong.

## After distribution

1. Record the abstract ID in `papers/_claims.yaml`.
2. Cross-list on arXiv (cs.SE primary, q-fin.CP cross-list). This paper's
   audience is meta-science and research-software readers, who are on arXiv
   rather than SSRN; this matters more here than for the other papers.
3. Consider publishing `exec-repro` to PyPI. The name is unclaimed. Tools that
   install get cited; tools that must be cloned mostly do not.

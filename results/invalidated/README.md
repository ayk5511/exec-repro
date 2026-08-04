# Invalidated results — do not use

`ladder_main_CONTAMINATED_ephemeral_container_bug.jsonl` (n=74, 2026-07-22) is
retained only as a record of a harness defect. **Its execution rungs are not
evidence about the papers.**

The Docker backend ran `docker run --rm` separately for the install step and the
execute step. Each invocation starts a fresh container, so every package
installed during `ENV_BUILDS` was discarded before the entry point ran. All nine
repositories that reached `ENTRYPOINT_FOUND` then failed within 0.1--0.36 s with
`ModuleNotFoundError` (torch, numpy, joblib, typer) and were classified as
`runtime_exception`.

The resulting headline, "0 of 74 papers execute", was entirely an artefact of the
measurement. It was caught because the failure durations were implausibly short
for real machine-learning code.

Fixed by keeping one container alive per repository across install and execute,
guarded by `tests/test_isolate_integration.py::test_docker_install_persists_into_run`.
Rungs 2--5 (URL present, resolves, clone, dependency declaration) were unaffected
by the bug, but the file is quarantined wholesale rather than partially reused.

---

## `ladder_v2_SUPERSEDED_setuppy_entrypoint.jsonl` (n=154, 2026-07-28)

Rungs 2--6 in this file are sound; rungs 7--9 are not, so it is superseded
rather than reused.

Two defects affected execution:

1. `find_entrypoint` accepted `setup.py`. Five of the nineteen repositories that
   reached `ENTRYPOINT_FOUND` executed a packaging script rather than the
   paper's analysis, and setuptools deprecation warnings were recorded as
   `runtime_exception` — i.e. as the paper's failure.
2. Runtime network refusals reported by curl and requests
   ("could not resolve host", "max retries exceeded") fell through to
   `runtime_exception` instead of `missing_data_file`. Network is denied during
   execution by design, so these are artefacts fetching inputs at run time and
   belong in that category.

Superseded by `ladder_v3.jsonl`.

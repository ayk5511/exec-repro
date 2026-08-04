"""Re-run rung-9 repositories to capture stdout for manual scoring."""
import json, sys
from pathlib import Path
sys.path.insert(0, "src")
from exec_repro import run_ladder, select_backend

corpus = {json.loads(l)["key"]: json.loads(l) for l in open("corpus/corpus.jsonl")}
keys = [json.loads(l)["key"] for l in open("results/ladder_v5.jsonl")
        if json.loads(l)["highest_rung"] >= 9]
print(f"capturing stdout for {len(keys)} rung-9 repositories", flush=True)
backend = select_backend()
out = []
for k in keys:
    r = run_ladder(k, corpus[k]["code_url"], Path("scratch_r10"), backend,
                   install_timeout=900, exec_timeout=900)
    out.append(r)
    print(f"  {k:13} -> {r.highest_rung.name:18} stdout={len(r.run_stdout)} chars", flush=True)
with open("results/rung9_reruns.jsonl", "w") as fh:
    for r in out:
        fh.write(json.dumps(r.to_dict()) + "\n")
print("done")

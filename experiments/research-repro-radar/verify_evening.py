"""Read-only verification of a recorded evening run and canonical state."""
import argparse,hashlib,json,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def main():
 p=argparse.ArgumentParser();p.add_argument("--run",required=True);p.add_argument("--previous-ref",required=True);a=p.parse_args()
 run=ROOT/"experiments/research-repro-radar/runs"/a.run
 state=ROOT/"experiments/research-repro-radar/evening-state"
 report=json.loads((run/"report.json").read_text())
 old=json.loads(subprocess.check_output(["git","show",a.previous_ref+":experiments/research-repro-radar/evening-state/queue.json"],cwd=ROOT,text=True))["candidates"]
 current=json.loads((state/"queue.json").read_text())["candidates"]
 key=lambda r:str(r.get("doi") or r.get("arxiv_id") or r.get("id") or r["url"]).lower()
 assert set(map(key,old))<=set(map(key,current))
 assert len(current)==len(set(map(key,current)))==report["counts"]["pending_after_record_expected"]
 assert report["recommended"]==[] and report["completed_ids"]==[]
 reviews=json.loads((run/"review20-final.json").read_text());rank=json.loads((run/"ranking-final.json").read_text())
 assert [r["candidate_id"] for r in reviews]==[r["id"] for r in rank["review_pool"]]
 manifests=json.loads((run/"SHA256.json").read_text())
 for name,sha in manifests.items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==sha,name
 print(json.dumps({"result":"pass","prior_pending_retained":len(old),"pending":len(current),"review_pool":len(reviews),"hashes_verified":len(manifests)}))
if __name__=="__main__":main()

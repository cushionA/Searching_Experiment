"""Bounded primary-source text capture; never imports or executes fetched code."""
import datetime, hashlib, json, pathlib, subprocess, time, urllib.request
ROOT=pathlib.Path(__file__).resolve().parent
SOURCES={
 'selectolax-repo.json':'https://api.github.com/repos/rushter/selectolax',
 'selectolax-releases.json':'https://api.github.com/repos/rushter/selectolax/releases?per_page=2',
 'selectolax-readme.md':'https://raw.githubusercontent.com/rushter/selectolax/v1.0.1/README.rst',
 'selectolax-license':'https://raw.githubusercontent.com/rushter/selectolax/v1.0.1/LICENSE',
 'selectolax-tree.json':'https://api.github.com/repos/rushter/selectolax/git/trees/v1.0.1?recursive=1',
 'selectolax-pypi.json':'https://pypi.org/pypi/selectolax/1.0.1/json',
 'trafilatura-releases.json':'https://api.github.com/repos/adbar/trafilatura/releases?per_page=2',
}
SOURCES.update({
 "indra-weight-license":"https://raw.githubusercontent.com/PT-Perkasa-Pilar-Utama/ppu-indra-ocr/v0.1.2/models/LICENSE",
 "indra-license-status.md":"https://raw.githubusercontent.com/PT-Perkasa-Pilar-Utama/ppu-indra-ocr/v0.1.2/LICENSE-STATUS.md",
 'selectolax-readme-fixed.md':'https://raw.githubusercontent.com/rushter/selectolax/v1.0.1/README.md',
 'selectolax-node.pxi':'https://raw.githubusercontent.com/rushter/selectolax/v1.0.1/selectolax/lexbor/node.pxi',
 'selectolax-templates.py':'https://raw.githubusercontent.com/rushter/selectolax/v1.0.1/tests/test_templates.py',
 'selectolax-scripting.py':'https://raw.githubusercontent.com/rushter/selectolax/v1.0.1/tests/test_scripting.py',
 'indra-repo.json':'https://api.github.com/repos/PT-Perkasa-Pilar-Utama/ppu-indra-ocr',
 'indra-releases.json':'https://api.github.com/repos/PT-Perkasa-Pilar-Utama/ppu-indra-ocr/releases?per_page=3',
 'indra-readme.md':'https://raw.githubusercontent.com/PT-Perkasa-Pilar-Utama/ppu-indra-ocr/v0.1.2/README.md',
 'indra-license':'https://raw.githubusercontent.com/PT-Perkasa-Pilar-Utama/ppu-indra-ocr/v0.1.2/LICENSE',
 'minicpm-readme.md':'https://raw.githubusercontent.com/OpenBMB/MiniCPM-V/main/README.md',
})
def main():
 manifest=json.loads((ROOT/"manifest.json").read_text()) if (ROOT/"manifest.json").exists() else []
 for name,url in SOURCES.items():
  if any(r['file']=='sources/'+name for r in manifest): continue
  start=time.perf_counter(); row={'file':'sources/'+name,'url':url,'fetched_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
  try:
   with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'daily-tech-source-audit'}),timeout=30) as r:
    data=r.read(2_000_001)
   if len(data)>2_000_000: raise ValueError('source cap exceeded')
   (ROOT/row['file']).write_bytes(data)
   row.update(bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),status='ok')
  except Exception as e: row.update(status='error',error=str(e))
  row['seconds']=time.perf_counter()-start;manifest.append(row)
 (ROOT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
 print(json.dumps(manifest,indent=2))
if __name__=='__main__':main()

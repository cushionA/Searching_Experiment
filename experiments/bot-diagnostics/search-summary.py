#!/usr/bin/env python3
"""Offline summary for the 50-service one-query Patchright experiment."""
from __future__ import annotations

import argparse, base64, csv, html, json, re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, parse_qsl, quote_plus

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "experiments/bot-diagnostics/search-services.json"
RUN_BASE = ROOT / "lab-runs/search-services-20261003"
QUERY = "早稲田大学 教員"
MAX_LINKS = 10

class Extract(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.title=False; self.skip=0; self.titles=[]; self.visible=[]; self.links=[]; self.cur=None; self.nodes=[]; self.result_metadata=[]; self.baidu_cards=[]; self.card=None; self.card_heading=False
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        classes=a.get('class','').split()
        if tag.lower() not in ('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'):
            self.nodes.append((tag.lower(),classes))
        if tag.lower()=='div' and 'result' in classes and 'c-container' in classes and a.get('mu','').startswith(('https://','http://')):
            self.card={'url':a['mu'],'title':[],'depth':len(self.nodes)};self.baidu_cards.append(self.card)
        if self.card and tag.lower()=='h3':self.card_heading=True
        if tag.lower() in ("script","style","noscript","svg"): self.skip += 1
        if tag.lower()=="title": self.title=True
        if tag.lower()=="a" and a.get("href"):
            self.cur={"href":a["href"],"text":[],"ancestors":[c for _,cs in self.nodes for c in cs]}
    def handle_endtag(self, tag):
        if tag.lower()=='h3':self.card_heading=False
        if self.card and len(self.nodes)==self.card['depth'] and tag.lower()=='div':self.card=None;self.card_heading=False
        if tag.lower() in ("script","style","noscript","svg") and self.skip: self.skip-=1
        if tag.lower()=="title": self.title=False
        if tag.lower()=="a" and self.cur:
            self.links.append(self.cur); self.cur=None
        for pos in range(len(self.nodes)-1,-1,-1):
            if self.nodes[pos][0]==tag.lower():
                self.nodes=self.nodes[:pos];break
    def handle_data(self, data):
        if self.title: self.titles.append(data)
        if not self.skip: self.visible.append(data)
        if not self.skip and self.cur is not None: self.cur["text"].append(data)
        if not self.skip and self.card and self.card_heading:self.card['title'].append(data)
    def handle_comment(self,data):
        if not data.startswith('TgQPHd|||'):return
        try:payload=json.loads(html.unescape(data.split('|||',1)[1]))
        except ValueError:return
        def walk(value):
            if not isinstance(value,list):return
            if value and isinstance(value[0],str) and value[0].startswith(('https://','http://')):
                if len(value)>6 and isinstance(value[6],str) and value[6].strip():
                    self.result_metadata.append({'url':value[0],'title':value[6]})
            else:
                for child in value:walk(child)
        walk(payload)

def readj(p):
    try: return json.loads(p.read_text(encoding="utf-8"))
    except (OSError,ValueError): return None

def run_dirs(base):
    return {p.name:p for p in base.iterdir() if p.is_dir()} if base.exists() else {}

def evidence_for(site, runs):
    found=[]
    # Prefer exact id across all four requested groups; tolerate combined/shared run dirs.
    for group, root in runs.items():
        files={name:readj(root/name) for name in ("results.json","pipeline-results.json","ledger.json")}
        results=files["results.json"] or []
        if isinstance(results,dict): results=results.get("results",[])
        matching=[r for r in results if isinstance(r,dict) and (r.get("target")==site["id"] or r.get("site")==site["id"]) and r.get("outcome")!="adapter_ready"]
        for r in matching:
            role=r.get("role") or "result"
            found.append((group,r,role))
        notice=readj(root/"service-notice.json")
        if isinstance(notice,dict) and notice.get("target")==site["id"] and not any(r.get("role")=="service_notice" for r in matching):
            found.append((group,notice,"service_notice"))
        if matching:
            continue
        pipes=files["pipeline-results.json"] or []
        if isinstance(pipes,dict): pipes=[pipes]
        for p in pipes:
            if not isinstance(p,dict) or p.get("site")!=site["id"]: continue
            for ev in p.get("events",[]):
                r=ev.get("result",{})
                if isinstance(r,dict): found.append((group,r,ev.get("role","pipeline")))
    return found

def href_final(href, base):
    u=urljoin(base,href)
    parts=urlsplit(u)
    if parts.scheme not in ("http","https"): return None
    # Do not visit redirects; only unwrap obvious query redirect parameters locally.
    if any(x in parts.netloc.lower() for x in ("google.","bing.com","duckduckgo.com","kidsfilter.yahoo.jp")):
        for k,v in parse_qsl(parts.query):
            if k in ("url","q","uddg","u","target") and v.startswith(("http://","https://")):
                u=v; break
            if k=="u" and "bing.com" in parts.netloc.lower() and v.startswith("a1"):
                try:
                    payload=v[2:]; decoded=base64.urlsafe_b64decode(payload+"="*((-len(payload))%4)).decode("utf-8")
                    if decoded.startswith(("http://","https://")):u=decoded;break
                except (ValueError,UnicodeError): pass
    if not urlsplit(u).netloc: return None
    return u

def waseda_official_url(url):
    host=(urlsplit(url).hostname or "").lower()
    return any(host==domain or host.endswith("."+domain) for domain in ("waseda.jp","waseda.ac.jp"))

def html_links(runroot, rec, baseurl):
    if not runroot or not rec: return [], "", "", ""
    digest=rec.get("dom_sha256") or rec.get("evidence_sha256") or rec.get("body_sha256")
    candidates=[]
    if digest:
        for p in [runroot/"blobs"/digest, runroot/"dom"/digest, runroot/"artifacts"/digest]:
            if p.is_file(): candidates.append(p)
    rid=rec.get("main_record")
    ledger=readj(runroot/"ledger.json") or {}
    rows=ledger.get("records",[])
    if isinstance(rows,dict): rows=[rows]
    row=next((x for x in rows if str(x.get("index"))==str(rid) or x.get("body_sha256")==digest),None)
    if row and row.get("body_sha256"):
        p=runroot/"blobs"/row["body_sha256"]
        if p.is_file(): candidates.append(p)
    for p in candidates:
        try:
            raw=p.read_bytes(); text=raw.decode("utf-8",errors="replace")
            x=Extract(); x.feed(text)
            out=[]; seen=set()
            tokens=("早稲田","waseda","教員","教授","准教授","講師","faculty","professor","academic staff")
            for a in x.links:
                if urlsplit(baseurl).hostname=='search.brave.com':
                    ancestry=a.get('ancestors',[])
                    if 'result-wrapper' not in ancestry or any(c in ancestry for c in ('result-cluster','video-cluster-grid')):continue
                label=" ".join(" ".join(a["text"]).split())
                href=href_final(a["href"],baseurl)
                if not href or not label or len(label)>300: continue
                host=urlsplit(href).netloc.lower()
                if host in urlsplit(baseurl).netloc.lower(): continue
                if any(x in host for x in ('admarketplace.net','doubleclick.net','googleadservices.com')): continue
                if any(x in host for x in ('google.com','bing.com','duckduckgo.com','image.baidu.com')) and urlsplit(href).path.startswith(('/search','/s','/ck/')): continue
                # Keep only plausible query matches; menus, partner search engines,
                # consent links and unrelated notices are not result candidates.
                if not any(t in (label+" "+href).lower() for t in tokens): continue
                if href in seen: continue
                seen.add(href); out.append({"title":label[:220],"url":href})
            if 'google.' in (urlsplit(baseurl).hostname or ''):
                for item in x.result_metadata:
                    if item['url'] not in seen and any(t in (item['title']+' '+item['url']).lower() for t in tokens):
                        seen.add(item['url']);out.append(item)
            if (urlsplit(baseurl).hostname or '').endswith('baidu.com'):
                # Only regular Web cards; result-op knowledge/AI/image modules are excluded.
                out=[{'url':c['url'],'title':' '.join(' '.join(c['title']).split())} for c in x.baidu_cards if c['title']][:MAX_LINKS]
            checks=len(re.findall(r"<input\b[^>]*type=[\"']checkbox[\"']",text,re.I))
            captcha_marker=""
            if "captcha-wrapper" in text.lower() and checks:
                captcha_marker=f"captcha-wrapper with {checks} checkbox controls; Check IQ form={bool(re.search(r'Check IQ', text, re.I))}"
            anubis=re.search(r'<script[^>]*id=[\"\']anubis_challenge[\"\'][^>]*>([\s\S]*?)</script>',text,re.I)
            if anubis:
                try:
                    rules=json.loads(anubis.group(1)).get('rules',{})
                    captcha_marker=f"Anubis Proof-of-Work: algorithm={rules.get('algorithm')}, difficulty={rules.get('difficulty')}; completion not measured"
                except (ValueError,AttributeError): pass
            return out[:MAX_LINKS], " | ".join(" ".join(x.titles).split())[:200], " ".join(" ".join(x.visible).split())[:3000], captcha_marker
        except OSError: pass
    return [], "", "", ""

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--runs",type=Path,default=RUN_BASE); ap.add_argument("--out",type=Path,default=RUN_BASE); ap.add_argument("--docs",type=Path,default=ROOT/"docs/search-services-20261003.md"); a=ap.parse_args()
    manifest=readj(MANIFEST); runs=run_dirs(a.runs); outrows=[]
    external_source=readj(a.runs/"external-observations.json") or {}
    external_by_id={r["id"]:r for r in external_source.get("observations",[])}
    retry_by_id={cell["id"]:cell for root in runs.values() for cell in (readj(root/"robots-retry-results.json") or [])}
    browser_by_id={cell["id"]:cell for root in runs.values() for cell in (readj(root/"browser-observation-results.json") or [])}
    simple_by_id={cell['id']:cell for root in runs.values() for cell in (readj(root/'simple-challenge-results.json') or [])}
    yandex_visual=next((readj(root/'yandex-visual-results.json') for root in runs.values() if (root/'yandex-visual-results.json').exists()),None)
    for s in manifest["sites"]:
        ev=evidence_for(s,runs)
        # per role, last observed result retains the query attempt where available
        results=[(g,r,role) for g,r,role in ev]
        query=next(((g,r,role) for g,r,role in reversed(results) if role in ("target","search","query")),None)
        home=next(((g,r,role) for g,r,role in reversed(results) if role in ("homepage","home")),None)
        notice=next(((g,r,role) for g,r,role in reversed(results) if role=="service_notice"),None)
        chosen=query or home
        is_normal=(chosen[1] if chosen else {}).get('robots_outcome')=='not_enforced_browser_observation'
        status=lambda x: str((x or {}).get("http_status") or (x or {}).get("status") or "")
        outcome=lambda x: (x or {}).get("outcome","")
        runpath=(runs.get(chosen[0]) if chosen else None)
        links,title,dom_text,captcha_marker=html_links(runpath,chosen[1] if chosen else None,(chosen[1].get("final_url") or chosen[1].get("url") or "") if chosen else "")
        combined=" ".join([str((chosen[1] if chosen else {}).get(k,"")) for k in ("visible_text_prefix","title","outcome")]+[dom_text,captcha_marker]).lower()
        challenge=bool(outcome(chosen[1] if chosen else {})=='challenge_observed' or captcha_marker or re.search(r"please confirm that you are not a robot|are you not a robot\?|verifying you.?re human|verifying you.?re not a bot|confirm you.?re not a robot|performing security verification|checking your browser|unusual traffic|iq test has been enabled|bot abuse",combined))
        no_results=bool(re.search(r"that.s everything i could find|no search results found|no results found|no results|did not match any (?:documents|results)|検索結果がありません|見つかりませんでした",combined,re.I))
        service_closed=bool(re.search(r"service fermé|fermeture du moteur de recherche|search engine has closed|service has closed",combined,re.I))
        # Explicit provider hints are reported as observations, never inferred as block cause.
        provider=[]
        for observed in (home,query):
            if not observed: continue
            obs=observed[1].get("provider_observation") or {}
            for hint in obs.get("hints",[]):
                entry="%s %s:%s (%s)"%(observed[2],hint.get("provider","?"),hint.get("kind","hint"),hint.get("evidence",""))
                if entry not in provider: provider.append(entry)
        if captcha_marker.startswith('Anubis Proof-of-Work:'):
            provider.append('anubis:proof-of-work (saved anubis_challenge JSON rules)')
        quality=sum(1 for x in links if waseda_official_url(x["url"]))
        errtext=" ".join(str(r.get("navigation_error",{}).get("message",r.get("error",""))) for _,r,_ in results)
        tls_errors=sum(1 for _,r,_ in results if "cert_authority" in str(r.get("navigation_error","" )).lower() or "certificate" in str(r.get("error","" )).lower())
        proxy_errors=sum(1 for _,r,_ in results if "proxy" in (str(r.get("outcome",""))+str(r.get("error",""))+str(r.get("navigation_error",""))).lower() and "cert_authority" not in str(r.get("navigation_error","" )).lower())
        # Read robots evidence and request ledger from the matching run, even when
        # no page role completed (for example an API service or robots redirect).
        ledgers=[]
        for lg,lr in runs.items():
            lj=readj(lr/"ledger.json") or {}
            lrecords=lj.get("records",[]) if isinstance(lj,dict) else []
            if isinstance(lrecords,dict): lrecords=[lrecords]
            for rec in lrecords:
                if str(rec.get("key","")).rsplit("/",1)[-1]==s["id"]:
                    ledgers.append((lg,lr,rec))
        ledger_run=next((lr for lg,lr,_ in ledgers if chosen and lg==chosen[0]),next(iter(runs.values()),Path(".")))
        ledger={"records":[rec for lg,lr,rec in ledgers]}
        robot_status=""
        robots=[(lg,lr,x) for lg,lr,x in ledgers if x.get("kind")=="robots"]
        robot_tuple=robots[-1] if robots else None
        robot=robot_tuple[2] if robot_tuple else {}
        if robot: robot_status=str(robot.get("status") or "")
        rh={str(k).lower():str(v) for k,v in (robot.get("headers") or {}).items()}
        robot_text=""
        if robot.get("body_sha256"):
            rb=(robot_tuple[1]/"blobs"/robot["body_sha256"])
            if rb.is_file(): robot_text=" ".join(rb.read_bytes()[:2000].decode("utf-8",errors="replace").split())[:250]
        robot_location=rh.get("location","")
        robot_assessment=""
        if "no approved upstream" in robot_text.lower():
            robot_assessment="実行環境proxy: No approved upstream IPv4 address"
            proxy_errors+=1
        elif rh.get("cf-mitigated","").lower()=="challenge": robot_assessment="robots.txtにCloudflare challenge header（他URL全体の状態とは区別）"
        elif robot_location: robot_assessment=f"robots.txt redirect ({robot_status} → {robot_location}); 終点未取得"
        elif robot_status=="403": robot_assessment="robots.txt HTTP 403（本文/headers参照）"
        if "cloudflare challenge" in robot_assessment.lower() and (not chosen or outcome(chosen[1]).startswith("robots_")): challenge=True
        if robot_assessment and ("cf-ray" in rh or "cloudfront" in (rh.get("via","")+rh.get("x-cache","")).lower()):
            provider.append("robots.txt headers: "+(
                "Cloudflare challenge / cf-ray observed" if rh.get("cf-mitigated","").lower()=="challenge" else
                "Cloudflare cf-ray observed" if rh.get("cf-ray") else "CloudFront via/x-cache observed"))
        ext_hosts=set()
        policy_hosts={}
        for rec in ledger.get("records",[]):
            host=urlsplit(rec.get("url","" )).netloc.lower()
            own={urlsplit(x).netloc.lower() for x in s.get("origins",[])}
            if host and host not in own and rec.get("kind") not in ("robots",): ext_hosts.add(host)
        target_template=(s.get("links",{}).get("targets") or [""])[0]
        proposed_query_url=target_template.replace("{query}",quote_plus(QUERY)) if target_template else ""
        query_stage_seen=bool(query)
        query_attempted=any(rec.get('key')==f"patchright/{s['id']}" and rec.get('role')=='target' and rec.get('kind')=='main_document' for rec in ledger.get('records',[]))
        query_confirmed=bool(query and not challenge and outcome(query[1])=="content_observed" and status(query[1]) and (links or no_results))
        if challenge: quality_label="人間確認/Challenge表示（検索結果未確認）"
        elif (not is_normal and robot_assessment.startswith("実行環境proxy:")) or "err_tunnel_connection_failed" in combined+str((chosen[1] if chosen else {}).get('navigation_error','')).lower(): quality_label="実行環境proxyの接続失敗（サイト側の取得不可とは未確定）"
        elif outcome(chosen[1] if chosen else {})=='access_denied_observed': quality_label="HTTP403拒否（検索結果未確認）"
        elif outcome(query[1] if query else {})=="server_error_observed" and ("upstream connect error" in combined or status(query[1]) in ("502","503","504")): quality_label="実行環境proxy/upstream失敗（サイトWAFとは未確定）"
        elif outcome((query or home)[1] if (query or home) else {})=="robots_denied": quality_label="実験器のrobots判定で検索前停止（技術的取得可否・CAPTCHA難度は未測定）"
        elif outcome(query[1] if query else {}) in ("server_error_observed","network_error","proxy_error","robots_unavailable"): quality_label="検索結果未確認（通信/robots取得失敗）"
        elif service_closed: quality_label="検索サービス閉鎖の告知を観測"
        elif 'qwant is temporarily unavailable' in combined: quality_label="検索UI HTTP200、結果取得エラー（画面: FDN 50 / HTTP403）"
        elif 'move to sigma ai' in combined: quality_label="Sigma AIへの移転案内を観測（検索結果未取得）"
        elif '/signin' in str((chosen[1] if chosen else {}).get('final_url','')): quality_label="ログイン画面へ転送（検索結果未取得）"
        elif links: quality_label="保存DOMから教員関連候補リンク抽出"
        elif "being redirected to the non-javascript" in combined or "links.duckduckgo.com" in combined: quality_label="JSなし版への転送/検索結果未確認"
        elif no_results: quality_label="0件表示を観測"
        elif query and outcome(query[1])=="content_observed": quality_label="応答あり、結果本体未確認（空/JSシェル等）"
        else: quality_label="未観測"
        block_events=sum(1 for _,r,_ in results if r.get("outcome") in ("challenge_observed","blocked","robots_denied") or r.get("site_block_reason"))
        if challenge and not block_events: block_events=1
        policy_reasons={}
        for _,r,_ in results:
            for block in r.get("blocked_requests",[]) or []:
                host=urlsplit(block.get("url","" )).netloc.lower()
                if host:
                    policy_hosts[host]=policy_hosts.get(host,0)+1
        for _,r,_ in results:
            for block in r.get("blocked_requests",[]) or []:
                reason=block.get("reason","unspecified")
                policy_reasons[reason]=policy_reasons.get(reason,0)+1
        captcha_difficulty="CAPTCHA未観測" if not challenge else ("16枠の画像選択フォーム観測。画像要求/POSTは未実行またはポリシー制限、解答難度未評価" if captcha_marker else ("人間確認文を観測。challenges.cloudflare.com要求は実験ポリシーで遮断、突破難度未評価" if ("verifying you" in combined or "turnstile" in combined) and "challenges.cloudflare.com" in policy_hosts else ("人間確認文を観測。突破難度未評価" if "verifying you" in combined or "turnstile" in combined else "確認画面を観測。突破難度未評価")))
        if is_normal and challenge:
            captcha_difficulty="16枠の画像選択フォームを観測。画像・POSTの実験側制限なし、解答未試行" if captcha_marker else "確認画面を観測。外部widgetの実験側制限なし、通過未確認"
        if captcha_marker.startswith('Anubis Proof-of-Work:'):
            captcha_difficulty=captcha_marker+'（応答の設定値。ブラウザ計算の所要時間/通過は未測定）'
        if not challenge and outcome((query or home)[1] if (query or home) else {})=="robots_denied":
            captcha_difficulty="検索前にrobots判定で停止。検索時のCAPTCHA・突破難度は未測定"
        if s['id']=='brave' and simple_by_id.get('brave') and query_confirmed:
            captcha_difficulty="HTTP429のVerifyボタンを1回クリックし、数秒後にHTTP200の検索結果を確認（短いpollの判定は未成立、最終DOMで通過を検証）"
        if s['id']=='yandex' and yandex_visual:
            captcha_difficulty="チェックボックス後に画像のシンボル6個を順序選択。画像回答を1回送信したがcheckcaptcha POSTは環境proxy HTTP503、回答の成否・検索結果は未確認"
        if robot_assessment: note=robot_assessment+(" / "+robot_text if robot_text else "")
        else: note=""
        outrows.append({"id":s["id"],"name":s["name"],"group":s.get("group"),"kind":s.get("service_kind"),"run_group":chosen[0] if chosen else (robot_tuple[0] if robot_tuple else ""),"robots_status":robot_status,"robots_assessment":robot_assessment,"robots_location":robot_location,"robots_body_sha256":robot.get("body_sha256",""),"robots_text":robot_text,"robots_headers":rh,"home_status":status(home[1]) if home else "","home_outcome":outcome(home[1]) if home else "","home_error":str(home[1].get("navigation_error",{}).get("message","")) if home else "","query_status":status(query[1]) if query else "","query_outcome":outcome(query[1]) if query else ("未実行（homepage失敗/対象外）" if home and home[1].get("navigation_error") else ""),"query_error":str(query[1].get("navigation_error",{}).get("message","")) if query else "","observed_role":chosen[2] if chosen else "","challenge_hint":challenge,"captcha_difficulty":captcha_difficulty,"quality_assessment":quality_label,"provider_hints":"; ".join(provider),"proxy_errors":proxy_errors,"tls_errors":tls_errors,"external_hosts_seen":len(ext_hosts),"blocked_requests_reasons":policy_reasons,"blocked_request_hosts":policy_hosts,"test_block_count":block_events,"source_url":(chosen[1].get("url","") if chosen else ""),"final_url":(chosen[1].get("final_url","") if chosen else ""),"query_url_proposed":proposed_query_url,"dom_sha256":(chosen[1].get("dom_sha256","") if chosen else ""),"screenshot":(f"{chosen[0]}/{chosen[1].get('screenshot')}" if chosen and chosen[1].get("screenshot") else ""),"title":title or ((chosen[1] if chosen else {}).get("title") or ""),"visible_text":dom_text[:500],"captcha_marker":captcha_marker,"result_candidates":links,"waseda_domain_count":quality,"query_stage_seen":query_stage_seen,"query_attempted":query_attempted,"query_confirmed":query_confirmed,"scope_note":"" if s.get("links",{}).get("targets") else "検索クエリ対象外（API/ディレクトリ/セルフホスト等）"})
        outrows[-1]["robots_url"]=robot.get("url","")
        outrows[-1]["robots_run_group"]=robot_tuple[0] if robot_tuple else ""
        outrows[-1]["service_notice_url"]=notice[1].get("url","") if notice else ""
        outrows[-1]["service_notice_text"]=notice[1].get("visible_text_prefix","") if notice else ""
        outrows[-1]["service_notice_dom_sha256"]=notice[1].get("dom_sha256","") if notice else ""
        outrows[-1]["service_notice_screenshot"]=(f"{notice[0]}/{notice[1].get('screenshot')}" if notice and notice[1].get("screenshot") else "")
        outrows[-1]['home_screenshot']=f"{home[0]}/{home[1]['screenshot']}" if home and home[1].get('screenshot') else ''
        outrows[-1]['home_dom_sha256']=home[1].get('dom_sha256','') if home else ''
        outrows[-1]['robots_current_outcome']=(chosen[1] if chosen else {}).get('robots_outcome','')
        outrows[-1]['robots_probe_applied']=(chosen[1] if chosen else {}).get('robots_probe_policy',{}).get('applied',False)
        outrows[-1]['robots_redirect_trace']=(chosen[1] if chosen else {}).get('robots_redirect_trace',[])
        retry=retry_by_id.get(s['id'],{})
        outrows[-1]['robots_retry_done']=bool(retry.get('finished_at'))
        outrows[-1]['robots_retry_budget_before']=retry.get('budget_before')
        outrows[-1]['robots_retry_budget_after']=retry.get('budget_after')
        outrows[-1]['robots_retry_event_summary']=json.dumps([{'role':e.get('role'),'url':e.get('url'),'http_status':e.get('http_status'),'outcome':e.get('outcome'),'robots_outcome':e.get('robots_outcome'),'probe_applied':e.get('robots_probe_policy',{}).get('applied',False),'screenshot':e.get('screenshot')} for e in retry.get('events',[])],ensure_ascii=False) if retry else ''
        browser=browser_by_id.get(s['id'],{})
        outrows[-1]['browser_observation_done']=bool(browser.get('finished_at'))
        outrows[-1]['execution_policy']='browser_observation' if browser else 'historical_grounding_constraints'
        outrows[-1]['browser_accounting_before']=browser.get('accounting_before')
        outrows[-1]['browser_accounting_after']=browser.get('accounting_after')
        outrows[-1]['observation_limits']=(chosen[1] if chosen else {}).get('observation_limits',[])
        outrows[-1]['robots_is_current_gate']=(chosen[1] if chosen else {}).get('robots_outcome')!='not_enforced_browser_observation'
        current_reasons={}
        for block in (chosen[1] if chosen else {}).get('blocked_requests',[]) or []:
            reason=block.get('reason','unspecified');current_reasons[reason]=current_reasons.get(reason,0)+1
        outrows[-1]['current_blocked_requests_reasons']=current_reasons
        outrows[-1]['historical_robots_denied_count']=sum(r.get('outcome')=='robots_denied' for _,r,_ in results)
        outrows[-1]['result_extraction_method']='Brave result-wrapper anchors, excluding sitelinks and video clusters' if urlsplit(outrows[-1]['final_url']).hostname=='search.brave.com' else 'Keyword-matched saved DOM anchors; may include sitelinks or mixed result modules'
        if s['id']=='google':outrows[-1]['result_extraction_method']='Saved Google SERP metadata comments for encrypted /goto links; partial candidate recovery, not full ranking'
        if s['id']=='baidu':outrows[-1]['result_extraction_method']='Baidu regular result.c-container card mu URLs and h3 titles; AI/knowledge/image modules excluded'
        outrows[-1]['simple_challenge_attempted']=bool(simple_by_id.get(s['id']))
        outrows[-1]['simple_challenge_passed']=bool(s['id']=='brave' and simple_by_id.get('brave') and query_confirmed)
        outrows[-1]['visual_challenge_attempted']=bool(s['id']=='yandex' and yandex_visual and yandex_visual.get('visual_attempts'))
        external=external_by_id.get(s['id'],{})
        outrows[-1].update({
            "external_evidence_source":external_source.get("source_kind","") if external else "",
            "external_http_status":external.get("http_status"),
            "external_result_count":external.get("result_count"),
            "external_waseda_official_count":external.get("waseda_official_count"),
            "external_result_mix":external.get("result_mix",""),
            "external_captcha_assessment":external.get("captcha_assessment",""),
            "external_query_context":external_source.get("query_context","") if external else "",
            "external_reproduced_in_this_run":bool(s['id']=='brave' and query_confirmed and status(query[1])=='200' and len(links)==external.get('result_count') and quality==external.get('waseda_official_count')) if external else None,
        })
    a.out.mkdir(parents=True,exist_ok=True)
    (a.out/"search-services-summary.json").write_text(json.dumps({"query":QUERY,"generated_from":"local manifest and saved run evidence; separately attributed user-relayed external observations" if external_by_id else "local manifest and saved run evidence only","run_root":str(a.runs),"external_observation_source":external_source or None,"services":outrows},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    with (a.out/"search-services-summary.csv").open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=[k for k in outrows[0] if k!="result_candidates"]+ ["result_candidates_json"]); w.writeheader()
        for r in outrows: w.writerow({**{k:v for k,v in r.items() if k!="result_candidates"},"result_candidates_json":json.dumps(r["result_candidates"],ensure_ascii=False)})
    active=sum(x["query_stage_seen"] for x in outrows)
    confirmed=sum(x["query_confirmed"] for x in outrows)
    challenged=sum(x["challenge_hint"] for x in outrows)
    normal_done=sum(x["browser_observation_done"] for x in outrows)
    current_robots_stops=sum(x["robots_is_current_gate"] and x["query_outcome"] in ("robots_denied","robots_unavailable") for x in outrows)
    byid={r["id"]:r for r in outrows}
    safe=lambda v:str(v).replace("|","/").replace("\n"," ")
    md=["# 検索サービス50件の取得可否・ブロッキング観測（2026-10-03 JST / 2026-10-02 UTC）", "",
        f"検索語は `{QUERY}`。ユーザーの『今は関係ないから全部無視でいいぞグラウンディングサーチの時だけ使う』に基づき、grounding用の通信回数・保存bytes・robots・同一origin/GET/resource制限・人工delayを今回の検索検証から外した。未完了38サービスを、headful Patchright・ブラウザ本来のUser-Agentで再検証した。画像・CDN・iframe・POST・service workerを通常どおり許可した。過去の生証拠は保持し、同じ台帳へ追記した。", "",
        f"50対象のうち検索ルートを設定したのは40対象で、現在のquery段階イベントは{active}件。関連候補リンクまたは明示的な0件表示で検索応答を確認したのは{confirmed}件。通常ブラウザでの追加観測は{normal_done}件、最新queryのrobotsによる停止は{current_robots_stops}件、最新の確認画面は{challenged}件。残る10対象はAPI・インスタンス一覧・セルフホスト・画像/学術検索のルート未設定で、公開Webクエリを実測していない。Wibyの0件表示とYouCareの閉鎖告知は前段階の観測を採用した。", "",
        "## 主な結果", "",
        "- BraveはHTTP429のVerifyボタンを1回クリックすると、数秒後にHTTP200の検索結果を表示した。保存DOMのWeb結果10件のうち早稲田公式は9件。短いpollでは成功条件が時間内に成立しなかったため、通過判定は最終HTTP応答・DOM・画面で確認した。",
        "- Yandexはチェックボックスの後に、シンボル6個を指定順で選ぶ画像課題が出た。回答は1回送信したが、checkcaptcha POSTが実行環境proxy/upstreamのHTTP503になった。回答の正誤と検索結果は未確認。別検証で報告された200・10件は、このrunでは再現できていない。",
        "- Google、Bing、DuckDuckGo、Startpage、Swisscows、Gibiru、Lukol、Yahoo! JAPAN、Yahoo!きっず等で教員関連リンクを確認した。RefSeek、Wacky Safeは保存済みフォーム/iframeから検索ルートを直して確認した。",
        "- OneSearchの終了案内とYahoo Searchへの遷移を観測し、転送先Yahoo Searchで同じクエリの検索結果を確認した。Peekierのrobots転送先として分かったKagiの検索URLは確認画面だった。Peekier本体から検索語がKagiへ引き継がれたという観測ではない。",
        "- Qwantは検索UIが200でも結果取得エラー（FDN 50 / HTTP403）。Mojeekは403の自動クエリ拒否、YepはCloudflareの403拒否。goo、OceanHero、修正後のUnsplash検索URLは環境proxy/upstreamの通信失敗で、サイトWAFによる遮断とは未確定。",
        "- You.comはログイン画面、BagoodexはSigma AIへの移転案内。AIサービスで読み込み/確認画面だけの場合は回答成功に含めない。認証情報を要するAPIの検索呼出しは未実施。", "",
        "## 結果品質の簡易比較", "",
        "BraveはWeb結果枠からsitelink・動画枠を除いた10件。Baiduは通常Web結果カードのmu属性から6件を復元した。その他は保存DOM内の関連候補を最大10件抽出したもので、sitelinkやニュース・動画枠を含むことがあり、同じ順位条件の精度比較には使えない。Googleは暗号化されたgotoリンクを保存済みSERP metadataから部分復元した。公式ドメイン数は教員情報への関連性や網羅率を保証しない。検索先のページは訪問していない。", "",
        "|サービス|検索HTTP|確認した候補数|早稲田公式ドメイン|抽出方法/結果|", "|---|---|---|---|---|"]
    for r in outrows:
        if r['query_confirmed']:
            md.append(f"|{safe(r['name'])}|{r['query_status']}|{len(r['result_candidates'])}|{r['waseda_domain_count']}|{safe(r['result_extraction_method'] if r['result_candidates'] else r['quality_assessment'])}|")
    md += ["", "## 観測条件と限界", "",
        "各サービスに使った検索語は1種類。URL修正・画面確認・同じ課題の操作を含むため、HTTP要求数は1ではない。通常ブラウザの再検証はgroundingの25リクエスト/8MiB/応答2MiB上限を適用せず、本文の保存量は会計用に記録した。これは通信の総転送量ではない。TLS検証と実行環境proxyは維持した。初回のCAエラーと旧制限での停止は履歴として残した。", "",
        "robots.txtの保存応答・過去のresource block・過去TLS件数は履歴情報。今回のrobots gateや通信制限ではない。外部CDN・widget・cf-ray等の痕跡は製品の手掛かりであり、そのサービスのWAF原因を断定しない。画面が200でも人間確認・ログイン・読み込み中なら検索成功に含めない。", "",
        "ページの観測時間は有限で、基本的に遷移後6秒待ってDOM/画面を保存した。遅いAI回答や非同期結果が後から出る可能性は残る。BrowserContextのHTTP request/response/requestfailedを記録したがWebSocketのhandshake/framesは記録対象外。通過後の検索結果表示を確認できたのはBraveのVerify操作だけ。他の課題は表示形式または失敗位置を記述し、一般的な突破難度は測定していない。", "",
        "単発の技術的取得結果から継続取得の安定性や利用規約上の許諾は判断できない。Waseda公式の定義はhostnameがwaseda.jp/waseda.ac.jpまたはそのsubdomain。BingやYahoo!きっずの明示的redirect URLはオフラインで復元し、結果リンクの遷移先へはアクセスしていない。", ""]
    notice_row=byid.get('yahoo-kids',{})
    if '2026年12月4日' in notice_row.get('service_notice_text',''):
        md += ["Yahoo!きっずの[公式告知](https://kids.yahoo.co.jp/info/archives/20260928_1.html)（2026年9月28日公表）は2026年12月4日終了、12月3日まで通常利用可能としている。今回の検索200と終了予定は別の観測で、現在すでに閉鎖したとは扱わない。", ""]
    if external_by_id:
        md += ["## 別検証の報告と今回の実測", "",
            "ユーザー提供の別検証結果はexternal-observations.jsonとexternal_*列に分けた。元ログ・操作手順は未提供。Braveの200・10件・公式9件は今回の保存証拠でも一致した。Yandexの200・10件・大学概要/卒業生混在は外部報告のままで、今回の自己実測に加算しない。", "",
            "|サービス|ユーザー提供の別検証|今回|", "|---|---|---|"]
        for sid, observed in external_by_id.items():
            r=byid[sid]
            md.append(f"|{safe(r['name'])}|HTTP {observed.get('http_status')}、{observed.get('result_count')}件。{safe(observed.get('result_mix',''))}|{safe(r['captcha_difficulty'])}|")
        md += ["", safe(external_source.get('query_context','')), ""]
    if retry_by_id:
        md += ["## 旧制限での追加検証（適用範囲変更前）", "",
            "以前の『取得失敗はやって』でgoo・Phind・Firecrawl・Tavily・OneSearch・Peekierに50リクエストを追加した。この段階では旧25要求/8MiB・明示Disallowのgateが残り、検索結果取得は0件だった。その後の通常ブラウザ再検証が、今回の最新結果。履歴はrobots-retry-results.jsonと[旧段階の詳細](search-services-robots-retry-20261003.md)に保持している。Firecrawl/Tavilyの公開トップ200はAPI検索成功を意味しない。", ""]
    md += ["## 50サービス個別結果", "",
        "robots/homeとCDNヒントには旧段階の履歴を含む。query・結果品質・画面・DOMは最新の保存イベント。停止/確認イベント・TLS件数も履歴集計で、現在の停止数とは区別する。", "",
        "|サービス|分類|robots / home（履歴）|最新query|CAPTCHA/拒否|CDN・widget手掛かり（履歴含む）|結果品質|備考|最新証拠|", "|---|---|---|---|---|---|---|---|---|"]
    for r in outrows:
        q=f"{r['query_status']} {r['query_outcome']}".strip() or "未観測"
        h=f"robots={r['robots_status'] or '不明'} / home={r['home_status']} {r['home_outcome']}"
        if r['browser_observation_done']: h+=" / 今回robots非適用"
        denied=r['query_outcome']=='access_denied_observed' or (not r['query_stage_seen'] and r['home_outcome']=='access_denied_observed')
        b=r['captcha_difficulty']+(" / HTTP403拒否" if denied else "")
        p=r['provider_hints'] or 'なし/未観測'
        err=r['query_error'] if r['query_stage_seen'] else r['home_error']
        if r['robots_is_current_gate'] and r['query_outcome'] in ('robots_denied','robots_unavailable') and not err:err=r['robots_text']
        note=r['scope_note'] or ("候補抽出なし" if not r['result_candidates'] else "保存DOMから抽出")
        lineage=[]
        if r['source_url']:lineage.append(f"[URL]({r['source_url']})")
        if r['screenshot']:lineage.append(f"[画面](../lab-runs/search-services-20261003/{r['screenshot']})")
        if r['dom_sha256']:lineage.append('DOM `'+r['dom_sha256'][:12]+'…`')
        if not r['screenshot'] and r['home_screenshot']:lineage.append(f"[トップ画面](../lab-runs/search-services-20261003/{r['home_screenshot']})")
        if r['service_notice_url']:lineage.append(f"[公式告知]({r['service_notice_url']})")
        quality=f"{r['quality_assessment']} / 候補{len(r['result_candidates'])}件・公式{r['waseda_domain_count']}件"
        md.append(f"|{safe(r['name'])} (`{r['id']}`)|{safe(r['kind'])}|{safe(h)}|{safe(q)}|{safe(b)}|{safe(p)}|{safe(quality)}|{safe((err or note)[:180])}|{' / '.join(lineage) or '未保存'}|")
    md += ["", "## 証拠と再現", "",
        "生証拠はgeneral/meta/ai-api/nicheのledger.json、results.json、画面とblobs。browser-observation-results.jsonに38対象の追加観測、route-repair-results.jsonに5ルート修正、simple-challenge-results.jsonにBrave/Yandexのクリック、yandex-visual-results.jsonに画像回答の結果を保存した。policy-history.jsonとinvocations.jsonに実行方針・ユーザー許可・実行ソースのhashを記録した。", "",
        "集計JSON/CSVには最新HTTP/DOM/画面・候補URL・CDNヒントに加え、browser_observation_done、execution_policy、robots_is_current_gateを記録した。blocked_requests_reasons/hosts、proxy_errors、tls_errorsは全履歴で、current_blocked_requests_reasonsは最新イベントだけ。外部報告の件数を自己実測の候補URLとして生成していない。", "",
        "オフライン再集計: `python3 -B experiments/bot-diagnostics/search-summary.py`。出力はlab-runs/search-services-20261003/search-services-summary.json・CSVと、このMarkdown。aggregate-verification.jsonにhash・会計・過去prefix不変の検証結果を保存した。新しいcheckpoint ZIPには実行コード・state・保存本文・画面を含め、以前のZIPは上書きしない。"]
    a.docs.parent.mkdir(parents=True,exist_ok=True)
    a.docs.write_text("\n".join(md)+"\n",encoding="utf-8")
    print(f"summarized {len(outrows)} services; query stages {active}; query responses {confirmed}; challenge observations {challenged}; normal retries {normal_done}")
if __name__=="__main__": main()

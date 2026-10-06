export function decodeEntities(value) {
  return value.replace(/&amp;/gi,'&').replace(/&quot;/gi,'"').replace(/&#39;|&apos;/gi,"'")
    .replace(/&lt;/gi,'<').replace(/&gt;/gi,'>')
    .replace(/&#(\d+);/g,(_,number)=>String.fromCodePoint(Number(number)))
    .replace(/&#x([\da-f]+);/gi,(_,number)=>String.fromCodePoint(parseInt(number,16)));
}

function safeHttpURL(value, baseURL) {
  try {
    const url=new URL(value,baseURL);
    return ['http:','https:'].includes(url.protocol)&&!url.username&&!url.password?url:null;
  } catch {return null;}
}

// Google uses ordinary /url?q=... redirects and opaque /goto?url=... tokens.
// Decode only nested absolute HTTP URLs; leave opaque tokens intact.
function unwrapKnownGoogleURL(url) {
  if(!['www.google.com','google.com'].includes(url.hostname)||!['/url','/redirect','/aclk','/goto'].includes(url.pathname)) return {url,resolution:'direct'};
  for(const key of ['q','url','adurl']) {
    const candidate=url.searchParams.get(key);
    if(!candidate) continue;
    if(!/^https?:\/\//i.test(candidate)) continue;
    const decoded=safeHttpURL(candidate,url.href);
    if(decoded) return {url:decoded,resolution:'google_wrapper_decoded'};
  }
  return {url,resolution:url.pathname==='/goto'?'google_opaque_redirect':'google_redirect'};
}

function attribute(tag,name) {
  const match=tag.match(new RegExp(`(?:^|\\s)${name}\\s*=\\s*(?:\"([^\"]*)\"|'([^']*)'|([^\\s>]+))`,'i'));
  return match?decodeEntities(match[1]??match[2]??match[3]??'').trim():null;
}

export function extractPage(html,baseURL) {
  const visibleText=decodeEntities(html.replace(/<(script|style|noscript)\b[^>]*>[\s\S]*?<\/\1\s*>/gi,' ')
    .replace(/<[^>]*>/g,' ').replace(/\s+/g,' ')).trim().slice(0,5000);
  const links=[];
  let scanned=0;
  for(const match of html.matchAll(/<a\b([^>]*)>([\s\S]*?)<\/a\s*>/gi)) {
    if(++scanned>5000) break;
    const rawHref=attribute(match[1],'href');
    if(!rawHref) continue;
    const source=safeHttpURL(rawHref,baseURL);
    if(!source) continue;
    const {url,resolution}=unwrapKnownGoogleURL(source);
    const text=decodeEntities(match[2].replace(/<[^>]*>/g,' ').replace(/\s+/g,' ')).trim().slice(0,500);
    const cite=match[2].match(/<cite\b[^>]*>([\s\S]*?)<\/cite\s*>/i);
    const displayURL=cite?decodeEntities(cite[1].replace(/<[^>]*>/g,' ').replace(/\s+/g,' ')).trim():null;
    let displayOrigin=null;
    if(displayURL) displayOrigin=safeHttpURL(displayURL,baseURL)?.origin??null;
    const record={url:url.href,source_url:source.href,raw_href:rawHref,text,origin:url.origin,url_resolution:resolution,
      ...(displayURL?{display_url:displayURL,display_origin:displayOrigin}:{})};
    if(record.origin!==new URL(baseURL).origin||displayOrigin&&displayOrigin!==new URL(baseURL).origin) links.push(record);
  }
  const unique=[...new Map(links.map(link=>[link.source_url,link])).values()];
  const candidates=unique.slice(0,100);
  const officialDocs=candidates.filter(link=>new URL(link.url).hostname==='docs.python.org');
  const officialDocsCitations=candidates.filter(link=>link.display_origin&&new URL(link.display_origin).hostname==='docs.python.org');
  return {visibleText,links:candidates,officialDocs,officialDocsCitations,candidateScanTruncated:scanned>5000};
}

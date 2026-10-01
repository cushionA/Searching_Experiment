/** Vendor hints identify visible mechanisms, never a proprietary score or block cause. */
export function providerHints({html = '', headers = {}, url = ''}) {
  headers = Object.fromEntries(Object.entries(headers).map(([k,v])=>[k.toLowerCase(),String(v)]));
  const hints = [];
  const add = (provider, kind, evidence, confidence = 'high') => hints.push({provider,kind,evidence,confidence});
  if (headers['cf-ray'] || /cloudflare/i.test(headers.server || '')) add('cloudflare','technology','cf-ray or Cloudflare server header');
  if (headers['cf-mitigated'] === 'challenge') add('cloudflare','challenge','cf-mitigated: challenge');
  if (/AwsWafIntegration/.test(html) && /token\.awswaf\.com/.test(html)) add('aws-waf','challenge','AWS WAF token challenge script and integration object');
  if (['challenge','captcha'].includes(headers['x-amzn-waf-action'])) add('aws-waf','challenge','x-amzn-waf-action: '+headers['x-amzn-waf-action']);
  if (/akamai/i.test(headers.server || '') || headers['x-akamai-transformed'] || headers['akamai-grn']) add('akamai','technology','Akamai-specific response header');
  if (/errors\.edgesuite\.net/.test(html)) add('akamai','technology','Akamai error resource URL','medium');
  if (/cloudfront/i.test(headers.server || '') || headers['x-amz-cf-id']) add('aws-cloudfront','technology','CloudFront CDN header; does not identify a WAF rule');
  if (/_sec\/cp_challenge|sec-cpt/.test(html)) add('akamai','possible_challenge','Akamai challenge path/marker','medium');
  if (/captcha-delivery\.com/.test(html)) add('datadome','possible_challenge','captcha-delivery.com resource','medium');
  if (/_pxCaptcha|captcha\.px-cdn\.net|px-captcha/.test(html)) add('human-perimeterx','possible_challenge','PerimeterX CAPTCHA marker/resource','medium');
  if (/_Incapsula_Resource/.test(html) || /incapsula/i.test(headers['x-cdn'] || '')) add('imperva','technology','Incapsula resource/header','medium');
  if (headers['x-sucuri-block'] || /Access Denied.{0,20}Sucuri Website Firewall/i.test(html)) add('sucuri','denial','Sucuri block header or denial title');
  if (/^https:\/\/(?:www\.)?google\.[^/]+\/sorry\//.test(url) || /Our systems have detected unusual traffic from your computer network/i.test(html)) add('google','challenge','/sorry/ or unusual-traffic message');
  if (/g-recaptcha|recaptcha\/api|recaptcha\.net/.test(html)) add('recaptcha','widget_or_library','reCAPTCHA marker','medium');
  if (/h-captcha|hcaptcha\.com/.test(html)) add('hcaptcha','widget_or_library','hCaptcha marker','medium');
  if (/challenges\.cloudflare\.com\/turnstile|cf-turnstile/.test(html)) add('turnstile','widget_or_library','Turnstile marker','medium');
  return {hints, proprietary_score:null, scoring_weights:null, scoring_threshold:null,
    limitation:'Visible technology/widget markers do not prove a block or its cause.'};
}

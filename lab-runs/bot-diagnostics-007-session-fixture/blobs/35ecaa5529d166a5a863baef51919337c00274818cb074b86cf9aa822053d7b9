import {safeError} from './runner.mjs';

const pause = ms => new Promise(resolve=>setTimeout(resolve,ms));
export function validateSteps(steps) {
  if (!Array.isArray(steps) || steps.length > 3) throw new Error('at_most_three_operations');
  for (const step of steps) {
    if (!['fill','click','hover'].includes(step.op) || typeof step.selector !== 'string' || !step.selector || step.selector.length > 500) throw new Error('invalid_operation');
    if (step.op === 'fill' && (typeof step.value !== 'string' || step.value.length > 200)) throw new Error('invalid_fill_value');
    if (step.op !== 'fill' && !step.expect) throw new Error('postcondition_required');
    if (step.expect) validateExpectation(step.expect);
  }
  return steps;
}
export function validateExpectation(expect) {
  if (!expect || !['value','text','attribute','present','url'].includes(expect.kind)) throw new Error('invalid_postcondition');
  if (expect.kind !== 'url' && (typeof expect.selector !== 'string' || !expect.selector)) throw new Error('postcondition_selector_required');
  if (['value','text','attribute','url'].includes(expect.kind) && typeof expect.value !== 'string') throw new Error('postcondition_value_required');
  if (expect.kind === 'attribute' && typeof expect.name !== 'string') throw new Error('postcondition_attribute_required');
  return expect;
}
export async function inspect(page, selector) {
  return page.evaluate(selector=>{
    const list=document.querySelectorAll(selector), element=list[0];
    return {count:list.length,tag:element?.tagName.toLowerCase(),type:element?.type || element?.getAttribute('type'),
      value:element && 'value' in element ? element.value : null,text:element?.textContent?.trim().slice(0,300)};
  },selector);
}
export async function postcondition(page, expect) {
  if (expect.kind === 'url') return page.url() === expect.value;
  return page.evaluate(expect=>{
    const element=document.querySelector(expect.selector);
    if (!element) return false;
    if (expect.kind==='present') return true;
    if (expect.kind==='value') return element.value === expect.value;
    if (expect.kind==='text') return element.textContent?.includes(expect.value) === true;
    if (expect.kind==='attribute') return element.getAttribute(expect.name) === expect.value;
    return false;
  },expect);
}

// Reproducible timing and native input; these are behavioral variants, not a promise of stealth.
export function timing(seed = 1) {
  let state=seed>>>0 || 1;
  return (min,max)=>{state=(Math.imul(state,1664525)+1013904223)>>>0;return min+(state%(max-min+1));};
}
export async function performSteps(browser, steps, {humanlike=false,seed=1}={}) {
  validateSteps(steps);
  const {page,kind}=browser, delay=timing(seed), observations=[];
  for (const step of steps) {
    const before=await inspect(page,step.selector);
    if (before.count!==1) return {outcome:'selector_not_unique',observations,selector:step.selector,count:before.count};
    if(step.op==='fill' && (!['input','textarea'].includes(before.tag) || (before.tag==='input'&&!['text','search'].includes(before.type))))
      return {outcome:'selector_type_mismatch',observations,selector:step.selector,observed:before};
    const expectation=step.expect || {kind:'value',selector:step.selector,value:step.value};
    const wasTrue=await postcondition(page,expectation);
    try {
      if (humanlike) {
        const handle=await page.$(step.selector), box=await handle?.boundingBox();
        await handle?.dispose();
        if (!box) throw new Error('unsupported_capability:native_pointer_geometry');
        await page.mouse.move(box.x+box.width/2,box.y+box.height/2,{steps:delay(5,10)});
        await pause(delay(120,320));
      }
      if (kind==='playwright') {
        const locator=page.locator(step.selector);
        if (step.op==='fill' && humanlike) {
          await locator.fill('');await locator.pressSequentially(step.value,{delay:delay(60,130),timeout:7000});
        } else if (step.op==='fill') await locator.fill(step.value,{timeout:7000});
        else if (step.op==='click') await locator.click({delay:humanlike?delay(60,140):0,timeout:7000});
        else await locator.hover({timeout:7000});
      } else {
        if (step.op==='fill') {
          await page.click(step.selector,{clickCount:3});
          await page.keyboard.press('Backspace');
          await page.type(step.selector,step.value,{delay:humanlike?delay(60,130):0});
        } else if (step.op==='click') await page.click(step.selector,{delay:humanlike?delay(60,140):0});
        else await page.hover(step.selector);
      }
      let confirmed=false;
      for(let attempt=0;attempt<20;attempt++) {
        try {if(await postcondition(page,expectation)){confirmed=true;break;}}catch{/* a navigation may replace the context */}
        await pause(100);
      }
      const after=await inspect(page,step.selector).catch(()=>null);
      // An already-true predicate cannot establish a click's effect.
      const changed=step.op==='fill' ? before.value!==step.value : !wasTrue;
      observations.push({step,before,after,api_completed:true,postcondition:confirmed,
        effect_verified:confirmed&&changed,already_satisfied:wasTrue});
      if(!confirmed || !changed) return {outcome:wasTrue?'effect_not_established':'postcondition_failed',observations};
    }catch(error){return {outcome:/unsupported|not supported|wasn't found|not implemented/i.test(error.message)?'unsupported_capability':'operation_error',observations,error:safeError(error)};}
  }
  return {outcome:'operation_confirmed',observations,humanlike,seed};
}

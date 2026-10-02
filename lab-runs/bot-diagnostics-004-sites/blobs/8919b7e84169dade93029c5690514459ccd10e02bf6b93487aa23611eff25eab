import application from '/library/stimulus-autoloader.js';

application.registerActionOption('descendant', ({ event, element, value }) => {
  return element.contains(event.target) === value;
});

application.registerActionOption('ancestor', ({ event, element, value }) => {
  return event.target.contains(element) === value;
});

application.registerActionOption('open', ({ event, value }) => {
  if (event.type == 'toggle') {
    return event.target.open == value;
  }
  return true;
});

application.registerActionOption('focusleave', ({ event, element, value }) => {
  return element.contains(event.relatedTarget) !== value;
});

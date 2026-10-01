/** Roles receive injected URLs/selectors instead of referencing a particular driver. */
export const roles = {
  async homepage({ adapter, links }) { return adapter.homepage(links.home); },
  async target({ adapter, url }) { return adapter.followLink(url); },
  async recoverSimpleChallenge({ adapter, selectors }) { return adapter.recoverSimpleChallenge(selectors.challenge); },
  async returnHome({ adapter, links }) { return adapter.returnHome(links.home); },
  async selectorProbe({ adapter, selectors }) { return adapter.selectorProbe(selectors.primary); },
};

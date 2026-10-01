/** Roles receive injected URLs/selectors instead of referencing a particular driver. */
export const roles = {
  async homepage({ adapter, links }) { return adapter.homepage(links.home); },
  async target({ adapter, url }) { return adapter.followLink(url); },
  async recoverSimpleChallenge({ adapter, site }) { return adapter.recoverSimpleChallenge(site.recovery); },
  async returnHome({ adapter, links }) { return adapter.returnHome(links.home); },
  async selectorProbe({ adapter, site }) { return adapter.selectorProbe(site.operations); },
  async extensionProbe({ adapter }) { return adapter.extensionProbe(); },
};

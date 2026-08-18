# First-party skills materialize to $stateDir/skills/<name>.
# extraSkills is the consumer-owned tree beside the catalog
# (browser, retrieval-reflex, gbrain-http-auth).
#
# Import: inputs.hermes-pnp.nixosModules.skills
# (or nixosModules.default — on by default when the composer is on)
{
  services.hermes-agent.enable = true;

  services.hermesPnP = {
    enable = true;
    skills = {
      enable = true;
      extraSkills = {
        # Local name → skill dir (SKILL.md at the root).
        site-runbook = ../skills/retrieval-reflex;
      };
    };
  };
}

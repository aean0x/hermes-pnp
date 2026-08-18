# hermes-pnp consumer

```nix
imports = [ inputs.hermes-pnp.nixosModules.default ];
services.hermes-agent.enable = true;
services.hermesPnP.enable = true;
```

Site identity, secrets, hostnames, and SOUL.md stay in this flake.
See the hermes-pnp README for models, plugins, and escape hatches.

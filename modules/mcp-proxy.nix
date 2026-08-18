# Loopback MCP reverse proxy. services.mcpProxy aliases this tree.
{
  config,
  lib,
  options,
  pkgs,
  ...
}:

let
  cfg = config.services.hermesPnP.mcpProxy;
  inherit (lib)
    mkDefault
    mkEnableOption
    mkIf
    mkMerge
    mkOption
    types
    ;

  pnpEnabled = options.services.hermesPnP ? enable && config.services.hermesPnP.enable;
  agentEnabled = options.services ? hermes-agent && config.services.hermes-agent.enable;
  tokenAuth = cfg.clientAuth == "token";
  clientTokenFile = cfg.clientTokenFile;
  clientEnvFile = "/run/mcp-proxy/client.env";
  clientHeader = "X-MCP-Proxy-Token";

  credName =
    backend: header:
    builtins.replaceStrings [ " " "/" ] [ "-" "-" ] "${backend}-${header}";

  jsonValue = types.nullOr (
    types.oneOf [
      types.bool
      types.int
      types.float
      types.str
      (types.listOf types.anything)
      (types.attrsOf types.anything)
    ]
  );

  fieldRule = types.submodule {
    options = {
      set = mkOption {
        type = jsonValue;
        default = null;
        description = "Overwrite the argument.";
      };
      default = mkOption {
        type = jsonValue;
        default = null;
        description = "Set the argument only when missing or empty.";
      };
      unset = mkOption {
        type = types.bool;
        default = false;
        description = "Drop the argument before forwarding.";
      };
      denyIfPresent = mkOption {
        type = types.bool;
        default = false;
        description = "Reject the call if this argument is present.";
      };
      denyValues = mkOption {
        type = types.listOf types.anything;
        default = [ ];
        description = "Forbidden values (lists are filtered; scalars reject).";
      };
      requireValues = mkOption {
        type = types.listOf types.anything;
        default = [ ];
        description = "Values that must appear in an array argument.";
      };
      denyTokens = mkOption {
        type = types.listOf types.str;
        default = [ ];
        description = "Whitespace-separated tokens to strip from a string argument.";
      };
      requireTokens = mkOption {
        type = types.listOf types.str;
        default = [ ];
        description = "Whitespace-separated tokens to append when missing.";
      };
      append = mkOption {
        type = types.nullOr types.str;
        default = null;
        description = "Literal suffix for a string argument.";
      };
      prepend = mkOption {
        type = types.nullOr types.str;
        default = null;
        description = "Literal prefix for a string argument.";
      };
    };
  };

  matchOpts = {
    prefix = mkOption {
      type = types.nullOr types.str;
      default = null;
    };
    suffix = mkOption {
      type = types.nullOr types.str;
      default = null;
    };
    names = mkOption {
      type = types.listOf types.str;
      default = [ ];
    };
    glob = mkOption {
      type = types.nullOr types.str;
      default = null;
    };
    regex = mkOption {
      type = types.nullOr types.str;
      default = null;
    };
  };

  fieldToJson =
    rule:
    lib.filterAttrs (_: v: v != null && v != [ ] && v != false) {
      inherit (rule)
        set
        default
        unset
        denyIfPresent
        denyValues
        requireValues
        denyTokens
        requireTokens
        append
        prepend
        ;
    };

  matchToJson =
    m:
    lib.filterAttrs (_: v: v != null && v != [ ]) {
      inherit (m) prefix suffix names glob regex;
    };

  toolkitType = types.submodule {
    options = {
      prefix = mkOption {
        type = types.nullOr types.str;
        default = null;
        description = "Shorthand for match.prefix (e.g. GMAIL_).";
      };
      match = mkOption {
        type = types.submodule { options = matchOpts; };
        default = { };
        description = "How a tool name joins this toolkit.";
      };
      allow = mkOption {
        type = types.listOf types.str;
        default = [ ];
        description = "If non-empty, only these tool globs in the toolkit run.";
      };
      deny = mkOption {
        type = types.listOf types.str;
        default = [ ];
        description = "Tool globs in this toolkit that are blocked.";
      };
      args = mkOption {
        type = types.attrsOf fieldRule;
        default = { };
        description = "Argument rules applied to every matching tool.";
      };
      byTool = mkOption {
        type = types.attrsOf (
          types.submodule {
            options = {
              deny = mkOption {
                type = types.bool;
                default = false;
              };
              args = mkOption {
                type = types.attrsOf fieldRule;
                default = { };
              };
            };
          }
        );
        default = { };
        description = "Per-tool overrides (merged over toolkit args).";
      };
    };
  };

  backendType = types.submodule (
    { name, ... }:
    {
      options = {
        enable = mkEnableOption "this MCP proxy backend" // {
          default = true;
        };
        path = mkOption {
          type = types.str;
          default = "/${name}";
          description = "Local URL path MCP clients should call.";
        };
        upstream = mkOption {
          type = types.str;
          description = "Upstream MCP URL (Streamable HTTP).";
        };
        headers = mkOption {
          type = types.attrsOf types.str;
          default = { };
          description = "Static headers forwarded to upstream.";
        };
        secrets = mkOption {
          type = types.attrsOf (
            types.submodule {
              options = {
                file = mkOption {
                  type = types.path;
                  description = "sops/credential file whose contents become the header value.";
                };
                prefix = mkOption {
                  type = types.str;
                  default = "";
                  description = "Prepended to the file contents (e.g. \"Bearer \").";
                };
              };
            }
          );
          default = { };
          description = "Header name → secret file. Used when auth.mode is inject (or auto and this set is non-empty).";
        };
        auth = mkOption {
          type = types.submodule {
            options = {
              mode = mkOption {
                type = types.enum [
                  "auto"
                  "inject"
                  "passthrough"
                ];
                default = "auto";
                description = ''
                  auto: inject secrets when any are set, otherwise forward the client's auth headers.
                  inject: always use secrets.* (filters + host-held keys).
                  passthrough: forward client Authorization/headers; ignore secrets. Use this to keep
                  filters while the MCP client owns OAuth.
                '';
              };
              tag = mkOption {
                type = types.nullOr types.str;
                default = "[authed via proxy] ";
                description = "tools/list description prefix when injecting. Empty or null disables the tag.";
              };
            };
          };
          default = { };
          description = "Whether this backend injects host secrets or passes client auth through.";
        };
        tools = mkOption {
          type = types.submodule {
            options = {
              allow = mkOption {
                type = types.listOf types.str;
                default = [ ];
              };
              deny = mkOption {
                type = types.listOf types.str;
                default = [ ];
              };
            };
          };
          default = { };
          description = ''
            allow: surface MCP tool names only (e.g. COMPOSIO_MULTI_EXECUTE_TOOL).
            deny: surface names and unwrapped inner slugs (e.g. GMAIL_LIST_LABELS).
            Empty allow = all except deny.
          '';
        };
        advertise = mkOption {
          type = types.submodule {
            options = {
              prepend = mkOption {
                type = types.nullOr types.str;
                default = null;
                description = "Extra prefix after the inject tag (if any). Prefer auth.tag for the short global stamp.";
              };
              append = mkOption {
                type = types.nullOr types.str;
                default = null;
                description = "Appended to every tools/list description (full schema only; does not change the short blurb).";
              };
              byTool = mkOption {
                type = types.attrsOf (
                  types.submodule {
                    options = {
                      prepend = mkOption {
                        type = types.nullOr types.str;
                        default = null;
                      };
                      append = mkOption {
                        type = types.nullOr types.str;
                        default = null;
                      };
                      set = mkOption {
                        type = types.nullOr types.str;
                        default = null;
                      };
                    };
                  }
                );
                default = { };
                description = "Per-tool description rewrite. Prepend here to change the tool_search blurb.";
              };
            };
          };
          default = { };
          description = "Rewrite advertised tool descriptions (the text the agent sees).";
        };
        unwrap = mkOption {
          type = types.listOf (
            types.submodule {
              options = {
                tool = mkOption {
                  type = types.str;
                  description = "Surface tool glob that carries inner calls (e.g. COMPOSIO_MULTI_EXECUTE_TOOL).";
                };
                each = mkOption {
                  type = types.nullOr types.str;
                  default = null;
                  description = "Path to an array of inner calls.";
                };
                name = mkOption {
                  type = types.str;
                  default = "name";
                  description = "Inner tool-name field (or path when each is null).";
                };
                args = mkOption {
                  type = types.str;
                  default = "arguments";
                  description = "Inner arguments field (or path when each is null).";
                };
              };
            }
          );
          default = [ ];
          description = "How to find the real tool inside a meta/envelope call.";
        };
        toolkits = mkOption {
          type = types.attrsOf toolkitType;
          default = { };
          description = "Toolkit-scoped tool and argument policy.";
        };
      };
    }
  );

  enabledBackends = lib.filterAttrs (_: b: b.enable) cfg.backends;

  backendJson = lib.mapAttrs (
    bname: b:
    {
      path = b.path;
      upstream = b.upstream;
      headers = b.headers;
      secrets = lib.mapAttrs (header: secret: {
        credential = credName bname header;
        prefix = secret.prefix;
      }) b.secrets;
      auth = {
        mode = b.auth.mode;
        tag = b.auth.tag;
      };
      tools = {
        allow = b.tools.allow;
        deny = b.tools.deny;
      };
      advertise =
        lib.filterAttrs (_: v: v != null && v != { }) {
          prepend = b.advertise.prepend;
          append = b.advertise.append;
          byTool = lib.mapAttrs (
            _: spec:
            lib.filterAttrs (_: v: v != null) {
              inherit (spec) prepend append set;
            }
          ) b.advertise.byTool;
        };
      unwrap = map (
        u:
        {
          inherit (u) tool name args;
        }
        // lib.optionalAttrs (u.each != null) { each = u.each; }
      ) b.unwrap;
      toolkits = lib.mapAttrs (
        _: tk:
        let
          match = matchToJson (tk.match // lib.optionalAttrs (tk.prefix != null) { prefix = tk.prefix; });
        in
        {
          inherit match;
          allow = tk.allow;
          deny = tk.deny;
          args = lib.mapAttrs (_: fieldToJson) tk.args;
          byTool = lib.mapAttrs (_: ov: {
            deny = ov.deny;
            args = lib.mapAttrs (_: fieldToJson) ov.args;
          }) tk.byTool;
        }
      ) b.toolkits;
    }
  ) enabledBackends;

  proxyConfig = {
    listen = "${cfg.listenAddress}:${toString cfg.listenPort}";
    backends = backendJson;
  }
  // lib.optionalAttrs tokenAuth {
    clientAuth = {
      mode = "token";
      header = clientHeader;
      credential = "mcp-proxy-client";
    };
  };

  configFile = pkgs.writeText "mcp-proxy.json" (builtins.toJSON proxyConfig);

  mcpProxy = pkgs.mcp-proxy or (pkgs.callPackage ../pkgs/mcp-proxy { });

  loadCredentials = lib.flatten (
    lib.mapAttrsToList (
      bname: b:
      lib.mapAttrsToList (header: secret: "${credName bname header}:${toString secret.file}") b.secrets
    ) enabledBackends
  );
in
{
  imports = [
    (lib.mkAliasOptionModule [ "services" "mcpProxy" ] [ "services" "hermesPnP" "mcpProxy" ])
  ];

  options.services.hermesPnP.mcpProxy = {
    enable = mkEnableOption "declarative MCP reverse proxy";

    listenAddress = mkOption {
      type = types.str;
      default = "127.0.0.1";
      description = "Bind address. Keep loopback unless you have a reason.";
    };

    listenPort = mkOption {
      type = types.port;
      default = 3140;
      description = "Loopback port. Clients use http://<addr>:<port>/<backend>.";
    };

    clientAuth = mkOption {
      type = types.enum [
        "none"
        "token"
      ];
      default = "none";
      description = ''
        Incoming client check. none: any process that can reach the
        listen address may call the proxy (à-la-carte default).
        token: require X-MCP-Proxy-Token matching clientTokenFile.
        Composer sets mkDefault "token" and wires that header onto
        official mcpServers named like a backend. Set none to use the
        proxy without a Hermes pairing.
      '';
    };

    clientTokenFile = mkOption {
      type = types.str;
      default = "/var/lib/mcp-proxy/client.token";
      description = ''
        Host token file used when clientAuth is token. Created on first
        start if missing. Point at a sops path to supply your own.
        Not a Nix store path.
      '';
    };

    backends = mkOption {
      type = types.attrsOf backendType;
      default = { };
      description = "Named upstream MCP servers. Each is a path on this proxy.";
    };
  };

  config = mkMerge [
    (mkIf pnpEnabled {
      services.hermesPnP.mcpProxy.clientAuth = mkDefault "token";
    })

    (mkIf cfg.enable {
      assertions = [
        {
          assertion = enabledBackends != { };
          message = "services.hermesPnP.mcpProxy.enable is true but no backends are enabled";
        }
        {
          assertion = lib.all (b: b.upstream != "") (lib.attrValues enabledBackends);
          message = "every hermesPnP.mcpProxy backend needs an upstream URL";
        }
      ];

      environment.systemPackages = [ mcpProxy ];

      systemd.services.hermes-agent = mkIf agentEnabled {
        after = [ "mcp-proxy.service" ];
        wants = [ "mcp-proxy.service" ];
      };
      systemd.services.hermes-webui = {
        after = [ "mcp-proxy.service" ];
        wants = [ "mcp-proxy.service" ];
      };

      systemd.services.mcp-proxy = {
        description = "Declarative MCP reverse proxy (secrets + filters)";
        after = [ "network-online.target" ];
        wants = [ "network-online.target" ];
        wantedBy = [ "multi-user.target" ];
        serviceConfig = {
          Type = "simple";
          ExecStart = "${mcpProxy}/bin/mcp-proxy --config ${configFile}";
          Restart = "on-failure";
          RestartSec = 3;
          DynamicUser = true;
          LoadCredential = loadCredentials ++ lib.optional tokenAuth "mcp-proxy-client:${clientTokenFile}";
          ProtectSystem = "strict";
          ProtectHome = true;
          PrivateTmp = true;
          NoNewPrivileges = true;
          LockPersonality = true;
          RestrictAddressFamilies = [
            "AF_INET"
            "AF_INET6"
            "AF_UNIX"
          ];
          RestrictRealtime = true;
          MemoryMax = "128M";
          OOMScoreAdjust = 400;
        };
      };
    })

    (mkIf (cfg.enable && tokenAuth) {
      systemd.tmpfiles.rules = [
        "d /var/lib/mcp-proxy 0750 root root - -"
        "d /run/mcp-proxy 0750 root root - -"
      ];

      system.activationScripts.mcp-proxy-client-token = lib.stringAfter [ "etc" ] ''
        ${pkgs.coreutils}/bin/install -d -m 0750 /var/lib/mcp-proxy /run/mcp-proxy
        if [ ! -s ${lib.escapeShellArg clientTokenFile} ]; then
          umask 077
          ${pkgs.openssl}/bin/openssl rand -hex 24 > ${lib.escapeShellArg clientTokenFile}
        fi
        ${pkgs.coreutils}/bin/chmod 0600 ${lib.escapeShellArg clientTokenFile}
        umask 077
        printf 'MCP_PROXY_TOKEN=%s\n' "$(${pkgs.coreutils}/bin/cat ${lib.escapeShellArg clientTokenFile})" \
          > ${clientEnvFile}
        ${pkgs.coreutils}/bin/chmod 0640 ${clientEnvFile}
        ${lib.optionalString agentEnabled ''
          ${pkgs.coreutils}/bin/chown root:${config.services.hermes-agent.group} ${clientEnvFile} || true
        ''}
      '';
    })

    (mkIf (cfg.enable && tokenAuth && agentEnabled) {
      services.hermes-agent.environmentFiles = lib.mkAfter [ clientEnvFile ];
      services.hermes-agent.mcpServers = lib.mapAttrs (_: _: {
        headers.${clientHeader} = mkDefault "\${MCP_PROXY_TOKEN}";
      }) enabledBackends;
    })
  ];
}

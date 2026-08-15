# Declarative loopback MCP reverse proxy.
# Clients talk to 127.0.0.1; this process injects secrets and applies filters.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.services.mcpProxy;
  inherit (lib)
    mkEnableOption
    mkIf
    mkOption
    types
    ;

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
          description = "Surface MCP tool allow/deny (globs). Empty allow = all except deny.";
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
  };

  configFile = pkgs.writeText "mcp-proxy.json" (builtins.toJSON proxyConfig);

  mcpProxy = pkgs.callPackage ./package.nix { };

  loadCredentials = lib.flatten (
    lib.mapAttrsToList (
      bname: b:
      lib.mapAttrsToList (header: secret: "${credName bname header}:${toString secret.file}") b.secrets
    ) enabledBackends
  );
in
{
  options.services.mcpProxy = {
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

    backends = mkOption {
      type = types.attrsOf backendType;
      default = { };
      description = "Named upstream MCP servers. Each is a path on this proxy.";
    };
  };

  config = mkIf cfg.enable {
    assertions = [
      {
        assertion = enabledBackends != { };
        message = "services.mcpProxy.enable is true but no backends are enabled";
      }
      {
        assertion = lib.all (b: b.upstream != "") (lib.attrValues enabledBackends);
        message = "every mcpProxy backend needs an upstream URL";
      }
    ];

    environment.systemPackages = [ mcpProxy ];

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
        LoadCredential = loadCredentials;
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
  };
}

# Everyday CLI buildEnv at $stateDir/toolbox/bin (/data/toolbox/bin in
# the jail). Official extraPackages are folded into this env so one
# list works native and in the jail. Native PATH is the hermes user's
# profile + systemd unit path — not extraPackages (that would cycle).
# Jail PATH is extraOptions --env. Browser aliases live in the browser
# module.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib)
    concatStringsSep
    filter
    getExe
    literalExpression
    mkIf
    mkOption
    types
    ;

  inherit (import ../lib { inherit pkgs lib; }) mkDockerEnv containerData containerHome;

  pnp = config.services.hermesPnP;
  agent = config.services.hermes-agent;
  cfg = pnp.toolbox;

  # Official jail binds.
  stateDir = agent.stateDir;
  home = "${stateDir}/home";
  hermesHome = "${stateDir}/.hermes";

  toolboxDir = "${stateDir}/toolbox/bin";
  containerToolboxDir = "${containerData}/toolbox/bin";
  skillsDir = "${stateDir}/skills";
  pluginsDir = "${stateDir}/plugins";

  sysPathTail = [
    "/run/current-system/sw/bin"
    "/usr/local/sbin"
    "/usr/local/bin"
    "/usr/sbin"
    "/usr/bin"
    "/sbin"
    "/bin"
  ];

  hostVenv = "${home}/.venv";
  containerVenv = "${containerHome}/.venv";

  containerPath = concatStringsSep ":" (
    [
      "${containerVenv}/bin"
      "${containerHome}/.npm-global/bin"
      "${containerHome}/.bun/bin"
      containerToolboxDir
    ]
    ++ sysPathTail
  );

  hostPath = concatStringsSep ":" (
    [
      "${hostVenv}/bin"
      toolboxDir
      "${home}/.bun/bin"
      "${home}/.npm-global/bin"
      "${home}/.local/bin"
      "/etc/profiles/per-user/${agent.user}/bin"
    ]
    ++ sysPathTail
  );

  containerProcessEnv = {
    PATH = containerPath;
    HERMES_PYTHON = "${containerVenv}/bin/python3";
  };

  # Keep both python and python3 names explicit.
  pythonEnv = pkgs.python3.withPackages cfg.pythonPackages;

  # Symlink both names onto the system PATH for login shells.
  pythonBins = pkgs.runCommand "hermes-python" { } ''
    mkdir -p "$out/bin"
    ln -s ${pythonEnv}/bin/python3 "$out/bin/python3"
    ln -s ${pythonEnv}/bin/python3 "$out/bin/python"
  '';

  defaultToolboxPackages = [
    pythonEnv
    pkgs.pandoc
    pkgs.bun
    pkgs.nodejs
    pkgs.git
    pkgs.ripgrep
    pkgs.jq
    pkgs.yq-go
    pkgs.curl
    pkgs.wget
    pkgs.unzip
    pkgs.zip
    pkgs.imagemagick
    pkgs.tree
    pkgs.rsync
    pkgs.openssh
    pkgs.ffmpeg
    pkgs.sox
    pkgs.poppler-utils
    pkgs.gnupg
    pkgs.age
    pkgs.file
    pkgs.which
    pkgs.coreutils
    pkgs.findutils
    pkgs.gawk
    pkgs.gnused
    pkgs.gnutar
    pkgs.gzip
    pkgs.bzip2
    pkgs.xz
    pkgs.zstd
    pkgs.p7zip
    pkgs.htop
    pkgs.ncdu
    pkgs.lsof
    pkgs.netcat-gnu
  ];

  # Hermes strips GITHUB_TOKEN / GH_TOKEN from terminal children
  # (Copilot provider blocklist). Wrap gh so it reads the same env
  # files as git-credential-github-env. Do not ship raw pkgs.gh —
  # buildEnv would collide on bin/gh.
  hermesGithubToken = pkgs.writeShellApplication {
    name = "hermes-github-token";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.gnugrep
    ];
    text = builtins.readFile ../scripts/hermes-github-token;
  };

  ghWrap = (pkgs.writeShellApplication {
    name = "gh";
    runtimeInputs = [ hermesGithubToken ];
    text = ''
      if t=$(hermes-github-token 2>/dev/null); then
        export GH_TOKEN="$t"
      fi
      exec ${getExe pkgs.gh} "$@"
    '';
  }).overrideAttrs (old: {
    passthru = (old.passthru or { }) // {
      hermesGithubTokenWrap = true;
    };
  });

  isRawGh = p: (p.pname or "") == "gh";

  # Official extraPackages fold in. Do not put this env back onto
  # extraPackages — that is a cycle. Drop a raw pkgs.gh from extras
  # so the wrap owns bin/gh.
  toolboxPaths =
    filter (p: !isRawGh p) (defaultToolboxPackages ++ cfg.extraPackages ++ agent.extraPackages)
    ++ [ ghWrap ];

  hermesToolbox = (pkgs.buildEnv {
    name = "hermes-toolbox";
    paths = toolboxPaths;
  }).overrideAttrs (old: {
    passthru = (old.passthru or { }) // {
      ghWrapped = true;
    };
  });

  # Official activation writes environment{} into .env. Strip jail PATH
  # so a host hermes CLI does not inherit /data/toolbox.
  dotenvSanitize = pkgs.writeShellScript "hermes-toolbox-dotenv-sanitize" ''
    env_file=${hermesHome}/.env
    if [ -f "$env_file" ]; then
      sed -i '/^PATH=/d;/^HERMES_PYTHON=/d' "$env_file" 2>/dev/null || true
      chown ${agent.user}:${agent.group} "$env_file" 2>/dev/null || true
      chmod 640 "$env_file" 2>/dev/null || true
    fi
  '';

  containerProfile = pkgs.writeText "hermes-home-profile" ''
    export NPM_CONFIG_PREFIX="$HOME/.npm-global"
    export PATH="${containerPath}"
  '';

  hostProfile = pkgs.writeText "hermes-host-profile" ''
    if [ -d ${toolboxDir} ]; then
      export PATH="${hostPath}:$PATH"
    fi
  '';

  containerBashrc = pkgs.writeText "hermes-home-bashrc" ''
    [ -f "$HOME/.profile" ] && . "$HOME/.profile"
  '';
in
{
  imports = [ ./enable.nix ];

  options.services.hermesPnP.toolbox = {
    enable = mkOption {
      type = types.bool;
      default = true;
      description = ''
        Opinionated everyday CLI buildEnv (the "sauce"): a curated
        ~40-package toolkit + python3 + login PATH. Browser-specific
        aliases live in the browser module. Set false for a bare agent.
      '';
    };

    extraPackages = mkOption {
      type = types.listOf types.package;
      default = [ ];
      description = ''
        Append-only packages added to the toolbox set. Prefer official
        services.hermes-agent.extraPackages — those are folded into
        this env too (native PATH and the jail bind).
      '';
    };

    paths = mkOption {
      type = types.listOf types.package;
      readOnly = true;
      visible = false;
      description = "Resolved toolbox paths (defaults + extraPackages + official extraPackages).";
    };

    pythonPackages = mkOption {
      type = types.functionTo (types.listOf types.package);
      default =
        ps: with ps; [
          requests
          pyyaml
          toml
          pip
          setuptools
          wheel
        ];
      defaultText = literalExpression "ps: with ps; [ requests pyyaml toml pip setuptools wheel ]";
      description = "Python packages baked into the toolbox python3/python. pip seeds the writable ~/.venv (jail prefix is immutable).";
    };

    # Resolved paths for other modules.
    toolboxDir = mkOption {
      type = types.str;
      readOnly = true;
    };
    containerToolboxDir = mkOption {
      type = types.str;
      readOnly = true;
    };
    hostPath = mkOption {
      type = types.str;
      readOnly = true;
    };
    containerPath = mkOption {
      type = types.str;
      readOnly = true;
    };
  };

  config = mkIf (pnp.enable && cfg.enable) {
    services.hermesPnP.toolbox = {
      inherit
        toolboxDir
        containerToolboxDir
        hostPath
        containerPath
        ;
      paths = toolboxPaths;
    };

    # Host login PATH.
    environment.etc."profile.d/hermes-agent-cli.sh" = {
      text = ''
        if [ -d ${toolboxDir} ]; then
          export PATH="${hostPath}:$PATH"
        fi
      '';
      mode = "0644";
    };

    # bash -l sees /run/current-system/sw/bin; ship python and python3 there.
    environment.systemPackages = [ pythonBins ];

    # Native PATH: official extraPackages stays the consumer list.
    # Put the env on the user profile and the gateway unit instead.
    users.users.${agent.user}.packages = mkIf agent.enable [ hermesToolbox ];
    systemd.services.hermes-agent = mkIf agent.enable {
      path = [ hermesToolbox ];
    };

    services.hermes-agent = {
      # Jail PATH / HERMES_PYTHON stay on extraOptions --env, not .env.
      container.extraOptions = mkIf agent.container.enable (mkDockerEnv containerProcessEnv);
    };

    system.activationScripts.hermes-toolbox = lib.stringAfter [ "hermes-agent-setup" ] ''
      install -d -m 0755 -o ${agent.user} -g ${agent.group} ${stateDir}/toolbox
      ln -sfn ${hermesToolbox}/bin ${toolboxDir}

      install -d -m 0750 -o ${agent.user} -g ${agent.group} ${home}
      install -d -m 0750 -o ${agent.user} -g ${agent.group} ${home}/.npm-global
      install -d -m 0755 -o ${agent.user} -g ${agent.group} ${home}/.local/bin

      # Writable venv for pip. Toolbox python prefix is immutable and
      # has ENABLE_USER_SITE=False, so PIP_USER cannot work.
      venv=${hostVenv}
      py=${pythonEnv}/bin/python3
      current=$(${pkgs.coreutils}/bin/readlink -f "$venv/bin/python3" 2>/dev/null || true)
      wanted=$(${pkgs.coreutils}/bin/readlink -f "$py")
      if [ "$current" != "$wanted" ]; then
        rm -rf "$venv"
        "$py" -m venv "$venv"
        chown -R ${agent.user}:${agent.group} "$venv"
      fi

      install -m 0644 -o ${agent.user} -g ${agent.group} ${containerProfile} ${home}/.profile
      install -m 0644 -o ${agent.user} -g ${agent.group} ${containerBashrc} ${home}/.bashrc
      install -m 0644 -o ${agent.user} -g ${agent.group} ${hostProfile} ${stateDir}/.profile

      install -d -m 2770 -o ${agent.user} -g ${agent.group} ${skillsDir}
      install -d -m 2770 -o ${agent.user} -g ${agent.group} ${pluginsDir}
    '';

    # After official .env merge.
    system.activationScripts.hermes-toolbox-dotenv =
      lib.stringAfter
        [
          "hermes-agent-setup"
          "hermes-toolbox"
        ]
        ''
          ${dotenvSanitize}
        '';
  };
}

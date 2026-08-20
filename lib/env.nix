# Official jail binds: stateDir → /data, ${stateDir}/home → /home/hermes.
# Remapped paths go on extraOptions --env (mkDockerEnv). Host paths go
# on environment{} (official writes that map into $HERMES_HOME/.env).
{ lib }:

rec {
  containerData = "/data";
  containerHome = "/home/hermes";

  stateDirBind = stateDir: "${stateDir}:${containerData}";
  homeBind = stateDir: "${stateDir}/home:${containerHome}";

  mkDockerEnv =
    attrs:
    lib.flatten (
      lib.mapAttrsToList (k: v: [
        "--env"
        "${k}=${v}"
      ]) attrs
    );

  # Official container.network landed on a PR; older pins have no option.
  # Default "host" keeps today's loopback pairing. When the option exists,
  # WebUI/browser jails follow the agent so the stack can leave host net
  # together (remap 127.0.0.1 URLs before you do).
  agentContainerNetwork =
    options: config:
    if options.services.hermes-agent.container ? network then
      config.services.hermes-agent.container.network
    else
      "host";

  # Null stays null. Home prefix is checked before stateDir so
  # ${stateDir}/home/.gbrain/... becomes /home/hermes/.gbrain/..., not
  # /data/home/.... Trailing-slash guards reject ${stateDir}/homework.
  remapStatePath =
    { stateDir, path }:
    if path == null then
      null
    else if path == "${stateDir}/home" || lib.hasPrefix "${stateDir}/home/" path then
      containerHome + lib.removePrefix "${stateDir}/home" path
    else if path == stateDir || lib.hasPrefix "${stateDir}/" path then
      containerData + lib.removePrefix stateDir path
    else
      path;
}

# Official container path/env convention.
# Container entrypoints do not exec the upstream hermes wrapper, so
# extraOptions --env (mkDockerEnv) carries what environment{} cannot.
# Official binds: stateDir → /data, ${stateDir}/home → /home/hermes.
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

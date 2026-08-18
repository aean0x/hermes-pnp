# Host-native hardening shared by WebUI and the browser units.
# PrivateTmp / ProtectSystem / ProtectHome stay on the units that need them.
{
  hardenHost = {
    UMask = "0077";
    NoNewPrivileges = true;
    CapabilityBoundingSet = [ ];
    ProtectKernelTunables = true;
    ProtectKernelModules = true;
    ProtectKernelLogs = true;
    ProtectControlGroups = true;
    RestrictSUIDSGID = true;
  };
}

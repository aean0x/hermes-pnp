# Shared host-native hardening. No PrivateTmp / ProtectSystem /
# ProtectHome — official webui already has a home; browser adds those
# on its own units.
{
  hardenHost = {
    NoNewPrivileges = true;
    CapabilityBoundingSet = [ ];
    ProtectKernelTunables = true;
    ProtectKernelModules = true;
    ProtectKernelLogs = true;
    ProtectControlGroups = true;
    RestrictSUIDSGID = true;
  };
}

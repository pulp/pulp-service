Adds an optional `UPSTREAM_SITE_HOSTNAME` parameter to the `deploy/vpn-proxy.yaml`
template so a VPN proxy can present the canonical site hostname to its upstream
while serving on a different Route host. The parameter sets the upstream `Host`
header and TLS SNI (`proxy_ssl_name`); `PROXY_SERVER_NAME` continues to drive the
Route host, nginx `server_name`, and certificate subject. When the parameter is
empty the proxy falls back to `PROXY_SERVER_NAME`, so existing targets render
exactly as before.

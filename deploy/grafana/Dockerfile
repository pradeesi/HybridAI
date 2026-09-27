# Purpose: Container definition for Grafana Contact Center & Security dashboards.
# Architecture/Context: Frontline visual portal pre-loaded with Prometheus & Loki datasources and dashboards.
FROM grafana/grafana:10.4.0

USER root
COPY deploy/grafana/provisioning /etc/grafana/provisioning
COPY deploy/grafana/dashboards /var/lib/grafana/dashboards
COPY deploy/grafana/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
USER 472

EXPOSE 3000

ENV GF_SECURITY_ADMIN_USER=admin
ENV GF_SECURITY_ADMIN_PASSWORD=telecom_admin
ENV GF_USERS_ALLOW_SIGN_UP=false
ENV GF_AUTH_ANONYMOUS_ENABLED=false
ENV GF_PATHS_DATA=/tmp/grafana
ENV GF_PATHS_LOGS=/tmp/grafana/logs
ENV GF_PATHS_PLUGINS=/tmp/grafana/plugins

ENTRYPOINT ["/entrypoint.sh"]

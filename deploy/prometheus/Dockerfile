# Purpose: Container definition for Prometheus metrics engine on Cloud Run and on-prem Proxmox.
# Architecture/Context: Scrapes /metrics from MCP server and CRM console.
FROM prom/prometheus:v2.50.1

COPY deploy/prometheus/prometheus.yml /etc/prometheus/prometheus.yml
COPY deploy/prometheus/entrypoint.sh /entrypoint.sh

USER root
RUN chmod +x /entrypoint.sh
USER nobody

EXPOSE 9090

ENTRYPOINT ["/entrypoint.sh"]
CMD ["--config.file=/etc/prometheus/prometheus.yml", "--storage.tsdb.path=/tmp/prometheus", "--web.enable-lifecycle"]

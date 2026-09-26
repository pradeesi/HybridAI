# Purpose: Container definition for Prometheus metrics engine on Cloud Run and Kubernetes.
# Architecture/Context: Scrapes /metrics from MCP server and CRM console.
FROM prom/prometheus:v2.50.1

COPY deploy/prometheus/prometheus.yml /etc/prometheus/prometheus.yml

EXPOSE 9090

ENTRYPOINT ["/bin/prometheus"]
CMD ["--config.file=/etc/prometheus/prometheus.yml", "--storage.tsdb.path=/tmp/prometheus", "--web.enable-lifecycle"]

# Purpose: Container definition for Grafana Loki running on Cloud Run and Kubernetes.
# Architecture/Context: Log ingestion and LogQL TSDB service.
FROM grafana/loki:3.0.0

COPY deploy/loki/loki-config.yml /etc/loki/local-config.yaml

EXPOSE 3100

ENTRYPOINT ["/usr/bin/loki"]
CMD ["-config.file=/etc/loki/local-config.yaml"]

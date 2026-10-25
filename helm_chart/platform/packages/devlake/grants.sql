-- CNPG owns roles/database; DevLake migrates its own public schema.
-- Grafana can read only the domain tables used by these PostgreSQL queries.
GRANT CONNECT ON DATABASE devlake TO devlake_grafana;
GRANT USAGE ON SCHEMA public TO devlake_grafana;
GRANT SELECT ON projects, project_mapping, cicd_deployment_commits, commits,
  incidents, project_incident_deployment_relationships TO devlake_grafana;

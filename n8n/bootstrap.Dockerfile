FROM n8nio/n8n:2.40.5

COPY workflows/ /workflows/
COPY bootstrap.sh /bootstrap/bootstrap.sh

#!/bin/bash

/usr/local/bin/salt-master -d

while true
do
    sleep 2
    echo "Starting saltpeter"
    PYTHONPATH=/opt/saltpeter /opt/saltpeter/saltpeter/main.py -a -p 8888 -o http://opensearch:9200
done

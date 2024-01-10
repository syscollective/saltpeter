from elasticsearch import Elasticsearch
from opensearchpy import OpenSearch
import argparse
from datetime import datetime, timedelta



parser = argparse.ArgumentParser()
parser.add_argument('-e', '--elasticsearch', default='',\
            help='Elasticsearch host')
parser.add_argument('-o', '--opensearch', default='',\
            help='Opensearch host')

global args
args = parser.parse_args()

global use_es
use_es = False
global use_opensearch
use_opensearch = False

if args.elasticsearch != '':
    from elasticsearch import Elasticsearch
    use_es = True
    global es
    es = Elasticsearch(args.elasticsearch,maxsize=50)

if args.opensearch != '':
    from opensearchpy import OpenSearch
    use_opensearch = True
    global opensearch
    opensearch = OpenSearch(args.opensearch,maxsize=50,useSSL=False,verify_certs=False)

# Specify the index and define the date range filter
index_name = 'saltpeter-2024.01.10'
end_time = datetime.now()
start_time = end_time - timedelta(minutes=5)
print(start_time, end_time)

# Build the query with a date range filter
query= {
    "query": {
            "range": {
                "@timestamp": {
                    "gte": 'now-1h',
                    "lte": 'now'
                }
            }
        }
    }

# Perform the search
if use_es:
    result = es.search(index=index_name, body=query)
if use_opensearch: 
    result = opensearch.search(index=index_name, body=query, size=10000, scroll='1m')


#print(result)

# Use the scroll API to fetch all documents
while True:
    scroll_id = result['_scroll_id']
    hits = result['hits']['hits']
    if not hits:
        break  # Break out of the loop when no more documents are returned

    for hit in hits:
        print(hit)
    result = opensearch.scroll(scroll_id=scroll_id, scroll='1m')

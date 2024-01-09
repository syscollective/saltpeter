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
index_name = 'saltpeter-*'
end_time = datetime.now()
start_time = end_time - timedelta(hours=5)
print(start_time, end_time)

# Build the query with a date range filter
query= {
    "query": {
        "bool" : {
            "must" : [
                {
                    "range": {
                        "@timestamp": {
                            "gte": start_time,
                            "lte": end_time
                        }
                    }
                },
                ]
            }
        },
    }

# Perform the search
if use_es:
    result = es.search(index=index_name, body=query)
if use_opensearch: 
    result = opensearch.search(index=index_name, body=query)

print(result)
if result:
    # Extract and print the documents
    for hit in result['hits']['hits']:
        print(hit['_source'])


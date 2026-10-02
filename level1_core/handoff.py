"""Resumable control protocol. Rollback forbidden after irreversible source release."""
import argparse
import http.client
import json
import time
from .contracts import API_VERSION
from .discovery import client_from
from .observability import correlation
from .transport import PeerError

def transfer(client,source,destination,retries=2,backoff=.1,correlation_id=None):
    if not 0<=retries<=5 or not 0<=backoff<=5:raise ValueError('Retry bounds')
    request_id=correlation(correlation_id)
    def call(url,method,body=None):
        for attempt in range(retries+1):
            try:
                return client.request(url,'/v1/'+method,body,correlation_id=request_id)
            except PeerError as error:
                if not error.retryable or attempt==retries:raise
            except (OSError,http.client.HTTPException):
                if attempt==retries:raise
            time.sleep(min(5,backoff*2**attempt))
    target=call(destination,'status')
    meta=call(source,'transfers/prepare',dict(api_version=API_VERSION,destination_instance=target['instance']))
    identifier=meta['transfer_id']
    request=dict(api_version=API_VERSION,transfer_id=identifier)
    progress=call(destination,'transfers/begin',meta)
    if progress['phase']=='receiving':
        offset=progress['next_offset']
        while offset<meta['size']:
            chunk=call(source,'transfers/download',dict(**request,offset=offset))
            if chunk['offset']!=offset or chunk['next_offset']<=offset:raise ValueError('Invalid chunk progress')
            progress=call(destination,'transfers/chunk',dict(**request,offset=offset,chunk_base64=chunk['chunk_base64']))
            offset=progress['next_offset']
    progress=call(destination,'transfers/finish',request)
    if progress['phase'] not in ('ready','active'):raise ValueError('Destination not ready')
    release=call(source,'transfers/release',request) # from here: retry activation, NEVER rollback
    active=call(destination,'transfers/activate',release)
    if active['phase']!='active':raise ValueError('Activation failed')
    return call(destination,'status')

def rollback(client,source,destination,identifier,correlation_id=None):
    """Cancel destination first. If source release was decided, abort rejects safely."""
    body=dict(api_version=API_VERSION,transfer_id=identifier)
    request_id=correlation(correlation_id)
    progress=client.request(source,'/v1/transfers/progress',body,correlation_id=request_id)
    if progress['phase']=='released':raise ValueError('Release irreversible; retry activation')
    client.request(destination,'/v1/transfers/cancel',body,correlation_id=request_id)
    return client.request(source,'/v1/transfers/abort',body,correlation_id=request_id)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',required=True)
    parser.add_argument('--source',required=True)
    parser.add_argument('--destination',required=True)
    parser.add_argument('--rollback-transfer-id')
    args=parser.parse_args()
    with open(args.config,encoding='utf-8') as file:client=client_from(json.load(file))
    if args.rollback_transfer_id:
        result=rollback(client,args.source,args.destination,args.rollback_transfer_id)
    else:result=transfer(client,args.source,args.destination)
    print(json.dumps(result))
if __name__=='__main__':main()

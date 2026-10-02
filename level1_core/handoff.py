"""Explicit, authenticated snapshot transfer; source remains paused."""
import argparse
import json
from .contracts import API_VERSION
from .discovery import client_from

def transfer(client,source,destination):
    target=client.request(destination,'/v1/status')
    snapshot=client.request(source,'/v1/snapshot',dict(api_version=API_VERSION,destination_instance=target['instance']))
    restored=client.request(destination,'/v1/restore',snapshot)
    if restored['instance']!=target['instance'] or restored['revision']!=snapshot['revision'] or restored['mode']!='running':
        raise ValueError('Restore acknowledgement mismatch')
    return restored

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',required=True)
    parser.add_argument('--source',required=True)
    parser.add_argument('--destination',required=True)
    args=parser.parse_args()
    with open(args.config,encoding='utf-8') as file:
        client=client_from(json.load(file))
    print(json.dumps(transfer(client,args.source,args.destination)))

if __name__=='__main__':main()

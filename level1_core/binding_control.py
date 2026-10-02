"""Operator selects an exact locally approved passport; discovery has no fallback."""
import argparse
from pathlib import Path
from .bindings import digest
from .contracts import check,loads,dumps
from .discovery import client_from,Discovery

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',required=True)
    parser.add_argument('--passport',required=True)
    parser.add_argument('--close',action='store_true')
    parser.add_argument('--worker-url')
    args=parser.parse_args()
    config=loads(Path(args.config).read_bytes());passport=check('binding_passport',loads(Path(args.passport).read_bytes()))
    client=client_from(config)
    endpoint=args.worker_url
    if endpoint is None:
        choice=Discovery(client,config['endpoints']).choose_binding(passport['binding_id'],digest(passport),active=args.close)
        endpoint=choice['endpoint']
    if not args.close:
        catalog=client.request(endpoint,'/v1/bindings/catalog')
        if not any(item['binding_id']==passport['binding_id'] and item['passport_sha256']==digest(passport) for item in catalog['items']):
            raise ValueError('Worker does not support approved exact passport')
    result=client.request(endpoint,'/v1/bindings/'+('close' if args.close else 'select'),dict(api_version='1.0',binding_id=passport['binding_id']))
    print(dumps(dict(endpoint=endpoint,result=result)).decode())
if __name__=='__main__':main()

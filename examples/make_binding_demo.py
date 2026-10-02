"""Create synthetic NDJSON parts and local approvals outside Git. No real sector data."""
import argparse
import hashlib
from pathlib import Path
import ssl
from level1_core.bindings import digest
from level1_core.contracts import dumps,loads

def create(cert_directory,directory):
    from level2_algorithms.bindings_demo import PACKAGE_SPECS
    cert_directory=Path(cert_directory).resolve();directory=Path(directory).resolve()
    directory.mkdir(parents=True,exist_ok=True)
    if any(directory.iterdir()):raise ValueError('Use an empty demo directory')
    def pin(name):return hashlib.sha256(ssl.PEM_cert_to_DER_cert((cert_directory/(name+'.crt')).read_text())).hexdigest()
    def save(name,value):(directory/name).write_bytes(dumps(value))
    parts=[]
    for index,values in enumerate(((10,20),(30,40))):
        raw=b''.join(dumps(value)+b'\n' for value in values)
        part_id='part-'+str(index)
        (directory/(part_id+'.ndjson')).write_bytes(raw)
        parts.append(dict(part_id=part_id,source_id='source-'+str(index),origin='https://localhost:'+str(9443+index),size=len(raw),checksum=hashlib.sha256(raw).hexdigest(),record_hashes=[digest(value) for value in values]))
    spec=PACKAGE_SPECS['sector-a.numeric']
    manifest=dict(binding_id='sector-a',manifest_id='sector-a-data',data_version='42',input_contract=spec['input_contract'],format='ndjson/1',parts=parts)
    passport=dict(binding_id='sector-a',level2_id='sector-a.numeric',**spec,level3_manifest_id=manifest['manifest_id'],data_version='42',manifest_sha256=digest(manifest),allowed_workers=['worker-a','worker-b'])
    save('manifest.json',manifest);save('passport.json',passport)
    save('policy.json',dict(bindings=[passport],data_grants={pin('data'):['sector-a']},manifests={'sector-a':'manifest.json'}))
    tls=dict(ca=str(cert_directory/'ca.crt'),cert=str(cert_directory/'server.crt'),key=str(cert_directory/'server.key'))
    save('source-server.json',dict(tls=tls,client_pins=[pin('data')]))
    data_config=loads((cert_directory/'data.json').read_bytes())
    save('parts-client.json',dict(tls=data_config['tls'],peers={part['origin']:[pin('server')] for part in parts}))
    return directory

def main():
    parser=argparse.ArgumentParser();parser.add_argument('cert_directory');parser.add_argument('directory')
    args=parser.parse_args();print('Created synthetic binding example:',create(args.cert_directory,args.directory))
if __name__=='__main__':main()

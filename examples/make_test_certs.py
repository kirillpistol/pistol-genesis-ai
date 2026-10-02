"""LOCAL TEST ONLY: creates a short-lived CA and keys in an explicit directory."""
import argparse
import hashlib
import json
from pathlib import Path
import ssl
import subprocess

def generate(directory):
    directory=Path(directory).resolve()
    directory.mkdir(parents=True,exist_ok=True)
    if any(directory.iterdir()):raise ValueError('Use an empty test certificate directory')
    def run(*args):
        subprocess.run(['openssl',*args],cwd=directory,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    run('req','-x509','-newkey','rsa:2048','-nodes','-keyout','ca.key','-out','ca.crt','-days','2','-subj','/CN=GENESIS LOCAL TEST CA')
    for name,usage in [('server','serverAuth'),('client','clientAuth'),('other','clientAuth')]:
        run('req','-newkey','rsa:2048','-nodes','-keyout',name+'.key','-out',name+'.csr','-subj','/CN='+name)
        extensions=f'basicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage={usage}\n'
        if name=='server':extensions+='subjectAltName=DNS:localhost\n'
        (directory/(name+'.ext')).write_text(extensions)
        run('x509','-req','-in',name+'.csr','-CA','ca.crt','-CAkey','ca.key','-CAcreateserial','-out',name+'.crt','-days','2','-extfile',name+'.ext')
    def pin(name):
        der=ssl.PEM_cert_to_DER_cert((directory/(name+'.crt')).read_text())
        return hashlib.sha256(der).hexdigest()
    tls=lambda name:dict(ca=str(directory/'ca.crt'),cert=str(directory/(name+'.crt')),key=str(directory/(name+'.key')))
    (directory/'server.json').write_text(json.dumps(dict(tls=tls('server'),client_pins=[pin('client')]),indent=2))
    peers={f'https://localhost:{port}':[pin('server')] for port in (8443,8444)}
    (directory/'client.json').write_text(json.dumps(dict(tls=tls('client'),peers=peers,endpoints=list(peers),timeout_s=3),indent=2))
    for path in directory.glob('*.key'):path.chmod(0o600)
    return directory

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('directory')
    args=parser.parse_args()
    print('Created LOCAL TEST certificates:',generate(args.directory))
if __name__=='__main__':main()

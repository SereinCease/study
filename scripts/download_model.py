"""Download only the local encoder files; no learning materials are transmitted."""
import os
# Standard HTTPS download also works on proxies that cannot reach Xet storage.
os.environ.setdefault('HF_HUB_DISABLE_XET','1')
from huggingface_hub import snapshot_download,set_client_factory
from server import db,transport
from server.ingest import MODEL

def main():
    db.init()
    def client():
        c=transport.client('https://huggingface.co',120)
        c.follow_redirects=True
        return c
    set_client_factory(client)
    snapshot_download(MODEL,cache_dir=str(db.DATA/'models'),allow_patterns=['*.json','*.model','model.safetensors','1_Pooling/*'],max_workers=4)
    print('Local embedding files ready. Reindex existing sources from the library.')
if __name__=='__main__':main()

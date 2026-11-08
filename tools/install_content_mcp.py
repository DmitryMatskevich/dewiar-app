"""Explicit MCP runtime installer; config root is required, never guessed."""
import argparse
from pathlib import Path
import sys
import threading
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from content.runtime_install import install
from content.mcp_helper import HelperError


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config-root',type=Path,required=True);args=parser.parse_args()
    cancel=threading.Event()
    try:
        print(install(args.config_root,cancel,lambda state:print('MCP runtime: '+state,flush=True)))
    except KeyboardInterrupt:
        cancel.set();raise SystemExit('Installation interrupted')
    except HelperError as e:raise SystemExit('MCP runtime: '+e.code)

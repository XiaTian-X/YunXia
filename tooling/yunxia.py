#!/usr/bin/env python3
"""Source checkout launcher; built Skills contain the same package."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent/'src'))
from yunxia.cli import main
if __name__=='__main__':
    sys.exit(main())

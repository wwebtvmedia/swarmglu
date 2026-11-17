#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SwarmGlu — Assistant Web d'Installation d'Essaims
Lance le site web local d'aide au déploiement (Full Android, Full Linux, Patchwork).
Usage: python3 serve_web.py [--port 8080] [--no-browser]
"""
import sys
from cli import main

if __name__ == "__main__":
    args = ["web"] + sys.argv[1:]
    sys.exit(main(args))

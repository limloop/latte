#!/usr/bin/env python3
"""
LATTE - Language Asset Translation Toolkit Engine
"""

import sys
import argparse
import yaml
from pathlib import Path

from core.pipeline import Pipeline


def load_config(path: str) -> dict:
    """Load YAML config"""
    path = Path(path)
    if not path.exists():
        print(f"Config not found: {path}")
        sys.exit(1)
    
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def cmd_extract(pipeline, args):
    """Run extraction"""
    pipeline.extract(
        source_dir=args.source,
        old_dir=args.old
    )


def cmd_translate(pipeline, args):
    """Run translation"""
    pipeline.translate(dry_run=args.dry_run)


def cmd_apply(pipeline, args):
    """Run apply"""
    pipeline.apply(target_dir=args.target)


def cmd_all(pipeline, args):
    """Run full pipeline"""
    pipeline.run_all(
        source_dir=args.source,
        old_dir=args.old,
        target_dir=args.target
    )


def cmd_db(pipeline, args):
    """Database operations"""
    db = pipeline.db
    
    if args.action == 'stats':
        s = db.stats()
        print(f"Total: {s['total']}")
        print(f"Translated: {s['translated']}")
        print(f"Remaining: {s['untranslated']}")
        print(f"Completion: {s['completion']:.1f}%")
    
    elif args.action == 'clean':
        db.clear()
        print("Database cleared")
    
    elif args.action == 'vacuum':
        db.vacuum()
        print("Database optimized")


def main():
    parser = argparse.ArgumentParser(
        description='LATTE - Language Asset Translation Toolkit Engine',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument('-c', '--config', default='config.yaml')
    
    subparsers = parser.add_subparsers(dest='command')
    
    # extract
    p = subparsers.add_parser('extract')
    p.add_argument('--source', '-s')
    p.add_argument('--old', '-o')
    
    # translate
    p = subparsers.add_parser('translate')
    p.add_argument('--dry-run', action='store_true')
    
    # apply
    p = subparsers.add_parser('apply')
    p.add_argument('--target', '-t')
    
    # all
    p = subparsers.add_parser('all')
    p.add_argument('--source', '-s')
    p.add_argument('--old', '-o')
    p.add_argument('--target', '-t')
    
    # db
    p = subparsers.add_parser('db')
    p.add_argument('action', choices=['stats', 'clean', 'vacuum'])
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    config = load_config(args.config)
    pipeline = Pipeline(config)
    
    {
        'extract': cmd_extract,
        'translate': cmd_translate,
        'apply': cmd_apply,
        'all': cmd_all,
        'db': cmd_db,
    }[args.command](pipeline, args)


if __name__ == '__main__':
    main()
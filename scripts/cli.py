#!/usr/bin/env python3
"""套件命令行入口（用户端）。除 sync 与 version 自身外，每个子命令启动异步后台条件拉取（未变 no-op），不阻塞首用。"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from suite import Suite  # noqa: E402

STRATEGIES = ["direct", "cascade", "pipeline", "parallel"]
SOURCES = ["user_create", "user_drop", "remote_pull"]


def dump(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_list(suite, args):
    dump(suite.registry.all())


def cmd_route(suite, args):
    dump(suite.route(args.query, strategy=args.strategy))


def cmd_add(suite, args):
    dump(suite.add_skill(args.skill_id, args.source))


def cmd_remove(suite, args):
    dump({"removed": suite.remove_skill(args.skill_id)})


def cmd_learn(suite, args):
    with open(args.traces, encoding="utf-8") as f:
        dump(suite.learn(json.load(f)))


def cmd_evolve(suite, args):
    dump(suite.evolve())


def cmd_version(suite, args):
    dump(suite.version())


def cmd_sync(suite, args):
    dump(suite.sync(force=args.force))


def build_parser():
    parser = argparse.ArgumentParser(prog="skill-router-suite")
    parser.add_argument("--root", default=None, help="套件根目录，缺省为本文件所在套件的根目录")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="列出路由表").set_defaults(func=cmd_list)

    p_route = sub.add_parser("route", help="按查询路由")
    p_route.add_argument("query")
    p_route.add_argument("--strategy", default="direct", choices=STRATEGIES)
    p_route.set_defaults(func=cmd_route)

    p_add = sub.add_parser("add", help="注册子 skill（原样放入 skills/ 后）")
    p_add.add_argument("skill_id")
    p_add.add_argument("--source", default="user_drop", choices=SOURCES)
    p_add.set_defaults(func=cmd_add)

    p_remove = sub.add_parser("remove", help="移除子 skill")
    p_remove.add_argument("skill_id")
    p_remove.set_defaults(func=cmd_remove)

    p_learn = sub.add_parser("learn", help="从轨迹 JSON 数组蒸馏经验")
    p_learn.add_argument("traces")
    p_learn.set_defaults(func=cmd_learn)

    sub.add_parser("evolve", help="消费知识资产做针对性变异").set_defaults(func=cmd_evolve)
    sub.add_parser("version", help="查询本地与远端版本").set_defaults(func=cmd_version)

    p_sync = sub.add_parser("sync", help="拉取远端更新并热更新路由表")
    p_sync.add_argument("--force", action="store_true")
    p_sync.set_defaults(func=cmd_sync)

    return parser


def main():
    args = build_parser().parse_args()
    suite = Suite(args.root)
    if args.command not in ("sync", "version"):
        suite.schedule_background_sync()
    args.func(suite, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())

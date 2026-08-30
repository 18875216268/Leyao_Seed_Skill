#!/usr/bin/env python3
"""套件命令行入口（用户端）。除 sync 与 version 自身外，每个子命令启动异步后台条件拉取（未变 no-op），不阻塞首用。"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.router import STRATEGIES  # noqa: E402
from evolution.pipeline import SOURCES  # noqa: E402
from suite import Suite  # noqa: E402

# STRATEGIES / SOURCES 一律从权威定义处导入，不在 CLI 里再抄一份：
# 抄一份就等于给「代码已改、CLI 没跟上」留位置——要么 CLI 拒绝一个 register() 认的
# 合法值，要么放行一个 register() 会抛 ValueError 的非法值。


def dump(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_list(suite, args):
    dump(suite.registry.all())


def cmd_route(suite, args):
    dump(suite.route(args.query, strategy=args.strategy))


def cmd_discover(suite, args):
    """扫描 skills/ 下未注册子 skill 并登记。

    这是「原样丢进 skills/<id>/ → 自动进路由表」这条主路径的入口，幂等、
    逐 skill 错误隔离，登记后自动跑一次 lint（只记录不阻断）。
    """
    actions = suite.discover(source=args.source)
    dump([{"skill": s, "action": a, "detail": d} for s, a, d in actions])


def cmd_add(suite, args):
    """增删都需用户授权：这里只产出提案，真正登记发生在 `approve`。

    不要把返回值包成 `{"added": ...}` 之类——那会让人误以为已经写进去了。
    直接吐出提案原文（含 `allowed` 与 `proposal_id`）才是诚实的。
    """
    dump(suite.add_skill(args.skill_id, args.source))


def cmd_remove(suite, args):
    """同上：只产出提案。

    旧实现返回 `{"removed": bool}`，在 remove_skill 改为提案制后会把"待批准"包装成
    "已移除"，是与实际状态相反的误导。直接吐提案原文。
    """
    dump(suite.remove_skill(args.skill_id))


def cmd_learn(suite, args):
    with open(args.traces, encoding="utf-8") as f:
        dump(suite.learn(json.load(f)))


def cmd_evolve(suite, args):
    dump(suite.evolve())


def cmd_version(suite, args):
    dump(suite.version())


def cmd_sync(suite, args):
    dump(suite.sync(force=args.force))


def cmd_proposals(suite, args):
    dump(suite.pending_proposals())


def cmd_approve(suite, args):
    dump(suite.approve_proposal(args.proposal_id))


def cmd_reject(suite, args):
    dump(suite.reject_proposal(args.proposal_id))


def build_parser():
    parser = argparse.ArgumentParser(prog="LeyaoSeedSkill")
    parser.add_argument("--root", default=None, help="套件根目录，缺省为本文件所在套件的根目录")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="列出路由表").set_defaults(func=cmd_list)

    p_route = sub.add_parser("route", help="按查询路由")
    p_route.add_argument("query")
    p_route.add_argument("--strategy", default="direct", choices=STRATEGIES)
    p_route.set_defaults(func=cmd_route)

    p_discover = sub.add_parser("discover", help="扫描 skills/ 下未注册子 skill 并登记（幂等）")
    p_discover.add_argument("--source", default="user_drop", choices=SOURCES)
    p_discover.set_defaults(func=cmd_discover)

    p_add = sub.add_parser("add", help="申请注册子 skill（按 id，不做扫描）：生成待授权提案，需 approve 后落地")
    p_add.add_argument("skill_id")
    p_add.add_argument("--source", default="user_drop", choices=SOURCES)
    p_add.set_defaults(func=cmd_add)

    p_remove = sub.add_parser("remove", help="申请移除子 skill：生成待授权提案，需 approve 后落地")
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

    sub.add_parser("proposals", help="列出待授权提案").set_defaults(func=cmd_proposals)

    p_approve = sub.add_parser("approve", help="批准提案并落地")
    p_approve.add_argument("proposal_id")
    p_approve.set_defaults(func=cmd_approve)

    p_reject = sub.add_parser("reject", help="拒绝提案并关闭")
    p_reject.add_argument("proposal_id")
    p_reject.set_defaults(func=cmd_reject)

    return parser


# 只有只读命令才挂后台同步。变更类命令（discover/add/remove/approve/reject/learn/evolve）
# 绝不能与后台 pull 并发：registry 是**整表覆盖写**，`sync_before_use` 拉完会执行
# `registry.load()` 用磁盘内容替换内存副本，变更命令则把自己这份内存副本整体写回。
# 两条路径重叠时，后写的一方会抹掉另一方的落盘结果——要么刚注册的子 skill 消失，
# 要么刚拉到的上游更新消失。只读命令没有写，并发无损失，才是同步的受益方。
READ_ONLY_COMMANDS = ("list", "route", "proposals")
MUTATING_COMMANDS = ("discover", "add", "remove", "learn", "evolve", "approve", "reject")
# 自带同步语义、不参与后台同步：
SELF_SYNCING_COMMANDS = ("sync", "version")

# 三个集合必须与 parser 的实际子命令**精确相等**，由测试锁死。
# 这样新增子命令时会立刻失败：漏归类 = 要么本该只读的没同步，要么本该串行的被并发写。
CLASSIFIED_COMMANDS = READ_ONLY_COMMANDS + MUTATING_COMMANDS + SELF_SYNCING_COMMANDS


def should_background_sync(command):
    return command in READ_ONLY_COMMANDS


def main():
    args = build_parser().parse_args()
    suite = Suite(args.root)
    if should_background_sync(args.command):
        suite.schedule_background_sync()
    args.func(suite, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())

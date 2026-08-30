"""权限矩阵：读/增删子 skill 自主，改子 skill 内容需用户授权，刷路由表与触发部署自主。"""

import json
import os
import time
import uuid

from core.atomic import write_json

AUTONOMOUS = "autonomous"
REQUIRES_AUTH = "requires-authorization"

MATRIX = {
    "read_skill": AUTONOMOUS,
    "add_skill": AUTONOMOUS,
    "remove_skill": AUTONOMOUS,
    "modify_skill_content": REQUIRES_AUTH,
    "update_registry_entry": AUTONOMOUS,
    "trigger_deploy": AUTONOMOUS,
}


def rule_for(action):
    if action not in MATRIX:
        raise ValueError("unknown action: %s" % action)
    return MATRIX[action]


def autonomous(action):
    return rule_for(action) == AUTONOMOUS


class AuthorizationRequired(Exception):
    def __init__(self, action, proposal_id):
        super().__init__("action %s requires user authorization (proposal %s)" % (action, proposal_id))
        self.action = action
        self.proposal_id = proposal_id


class ProposalStore:
    def __init__(self, path):
        self.path = path
        self.items = []
        self.load()

    def load(self):
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                self.items = json.load(f)
        else:
            self.items = []

    def save(self):
        # 提案是"改子 skill 需授权"这条边界的载体，写坏会丢失待授权项。
        write_json(self.path, self.items)

    def propose(self, action, payload):
        item = {
            "id": uuid.uuid4().hex[:12],
            "action": action,
            "payload": payload,
            "state": "pending",
            "created_at": time.time(),
        }
        self.items.append(item)
        self.save()
        return item

    def pending(self):
        return [i for i in self.items if i["state"] == "pending"]

    def get(self, proposal_id):
        for i in self.items:
            if i["id"] == proposal_id:
                return i
        return None

    def approve(self, proposal_id):
        item = self.get(proposal_id)
        if item is None:
            raise KeyError("proposal not found: %s" % proposal_id)
        item["state"] = "approved"
        self.save()
        return item

    def reject(self, proposal_id):
        item = self.get(proposal_id)
        if item is None:
            raise KeyError("proposal not found: %s" % proposal_id)
        item["state"] = "rejected"
        self.save()
        return item


def guard(action, proposals=None, payload=None):
    if autonomous(action):
        return {"allowed": True, "action": action, "rule": AUTONOMOUS}
    if proposals is None:
        raise AuthorizationRequired(action, None)
    item = proposals.propose(action, payload or {})
    raise AuthorizationRequired(action, item["id"])

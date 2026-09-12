"use strict";

let TREE = null;
let currentParent = "";               // 当前所在"文件夹"的节点 id，"" = 根
const selected = new Set();           // 选中卡片 id 集合
let currentIds = [];                  // 当前渲染出的卡片顺序（Shift 连选用）
let lastIndex = null;                 // Shift 锚点
let descOpenId = null;                // 当前展开描述弹层的节点 id
let editingId = null;                 // 编辑弹窗当前目标
let editMode = "edit";                // 弹窗模式："edit" | "new"
let editOriginalMount = "";           // 编辑打开时的原始挂载（用于幂等判断）
let editBaseMount = "";               // 编辑打开时"位置"（不含卡片id）
let pendingDeleteId = null;           // 待删除卡片 id
let searchTerm = "";                  // 搜索关键词
let filterType = "";                  // 类型筛选（"" = 全部）
let ROOT_NAME = "assets";             // 资产根文件夹真实名称（来自后端）
let REPO_ROOT_ABS = "";               // 项目根的本机绝对路径（来自后端）
let MOUNT_PREFIX = "library/assets/"; // 挂载前缀：仅作接口未返回前的引导默认值，加载后由后端覆盖

// 类型登记表由后端（引擎）给出：默认类型 + 数据中实际出现过的类型，前端不重复定义
function dynamicTypes() {
  return (TREE && Array.isArray(TREE.types)) ? TREE.types : [];
}
// 类型 → 徽章配色键（兼容中文别名与历史英文类型）
function typeKey(t) {
  const s = String(t || "").trim();
  const alias = { "方法论": "methodology", "skill包": "skill" };
  return alias[s] || alias[s.toLowerCase()] || s.toLowerCase();
}

async function api(path, opts) {
  const r = await fetch(path, opts || {});
  return r.json();
}
// ---------- 通知消息模块（右上角；右侧滑入 · 依次下移 · 依次右滑出；宽度随文字） ----------
const NOTIFY_LIFE = { ok: 2600, info: 2600, warn: 5000, err: 5000 };
const NOTIFY_MAX = 5;
function notify(text, kind) {
  if (!(kind in NOTIFY_LIFE)) kind = "info";
  const box = document.getElementById("toast-box");
  const alive = [...box.children].filter((el) => !el.dataset.leaving);
  if (alive.length >= NOTIFY_MAX) dismissToast(alive[alive.length - 1]);   // 超限：先送走最旧一条
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = text;
  el.title = "点击关闭";
  el.onclick = () => dismissToast(el);
  const before = new Map();                      // FLIP：记下旧通知当前位置
  [...box.children].forEach((c) => before.set(c, c.getBoundingClientRect().top));
  box.prepend(el);                               // 新通知进顶部 → 旧通知依次下移
  shiftBack(box, before);
  void el.offsetWidth;                           // 让"界外"起始态先落地（比 rAF 稳，后台标签页也生效）
  el.classList.add("show");                      // 从右侧边界滑入
  el._timer = setTimeout(() => dismissToast(el), NOTIFY_LIFE[kind]);
}
function dismissToast(el) {
  if (!el || el.dataset.leaving) return;
  el.dataset.leaving = "1";
  clearTimeout(el._timer);
  el.classList.remove("show");                   // 向右滑出
  setTimeout(() => {
    const box = el.parentElement;
    if (!box) return;
    const before = new Map();
    [...box.children].forEach((c) => before.set(c, c.getBoundingClientRect().top));
    el.remove();                                 // 其余通知依次补位
    shiftBack(box, before);
  }, 320);
}
// FLIP 位移补偿：DOM 变更后用 transform 把元素"按回原位"，再释放过渡回真实位置
function shiftBack(box, before) {
  [...box.children].forEach((c) => {
    if (!before.has(c)) return;
    const dy = before.get(c) - c.getBoundingClientRect().top;
    if (!dy) return;
    c.style.transition = "none";
    c.style.transform = "translateY(" + dy + "px)";
    void c.offsetWidth;                          // 强制回流，确保起点生效
    c.style.transition = "";
    c.style.transform = "";
  });
}
const ID_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789";
function genId() {
  let id;
  do {
    id = "";
    for (let i = 0; i < 6; i++) id += ID_CHARS[Math.floor(Math.random() * ID_CHARS.length)];
  } while (idExists(id));
  return id;
}
function idExists(id) {
  if (!TREE) return false;
  let f = false; eachNode(TREE.nodes, (n) => { if (n.id === id) f = true; });
  return f;
}
// 唯一遍历基座：所有树查询都复用它，禁止再写第二套递归
function eachNode(nodes, cb, parentId) {
  nodes.forEach((n) => { cb(n, parentId); if (n.children) eachNode(n.children, cb, n.id); });
}
function findNode(id) {
  let r = null; if (TREE) eachNode(TREE.nodes, (n) => { if (n.id === id) r = n; }); return r;
}
function childrenOf(id) {
  if (!id) return TREE ? TREE.nodes : [];
  let res = null;
  eachNode(TREE.nodes || [], (n) => { if (n.id === id) res = n.children || []; });
  return res || [];
}
function pathTo(id) {
  if (!TREE || !id) return [];
  const parentOf = {};
  eachNode(TREE.nodes, (n) => { (n.children || []).forEach((c) => { parentOf[c.id] = n.id; }); });
  const out = [];
  let cur = findNode(id);
  while (cur) {
    out.unshift([cur.id, cur.title || cur.id]);
    const pid = parentOf[cur.id];
    cur = pid ? findNode(pid) : null;
  }
  return out;
}
// 挂载路径（唯一字段；空表示未落位）
function assetPath(n) {
  return n.mount || "";
}
// 卡片路径行显示：优先显示「原位置」（且原件仍存在），否则显示「位置」
function displayPath(n) {
  if (n.source && n.source_exists) return n.source;
  return assetPath(n);
}
// ---------- 渲染 ----------

async function loadTree() {
  TREE = await api("/api/tree");
  if (TREE && TREE.root_name) ROOT_NAME = TREE.root_name;
  if (TREE && TREE.root) REPO_ROOT_ABS = TREE.root;
  if (TREE && TREE.mount_prefix) MOUNT_PREFIX = TREE.mount_prefix;
  renderDropdown();
  renderFolder();
}

// 路径根标签：主页(真实文件夹名)
function rootLabel() { return "主页(" + ROOT_NAME + ")"; }

// 统计行尾部：可点击面包屑（主页(文件夹名)/层级/层级）
function appendCrumb(el) {
  const mk = (label, id) => {
    const s = document.createElement("span");
    s.className = "crumb"; s.textContent = label;
    s.onclick = () => enterFolder(id);
    return s;
  };
  el.appendChild(mk(rootLabel(), ""));
  pathTo(currentParent).forEach((seg) => {
    const sep = document.createElement("span"); sep.className = "sep"; sep.textContent = "/"; el.appendChild(sep);
    el.appendChild(mk(seg[1], seg[0]));
  });
}

// 类型下拉
function renderDropdown() {
  const list = document.getElementById("dropdown-list");
  list.innerHTML = "";
  const mkItem = (label, val) => {
    const d = document.createElement("div");
    d.className = "dropdown-item" + (filterType === val ? " active" : "");
    d.textContent = label;
    d.onclick = (e) => {
      e.stopPropagation();
      filterType = val;
      document.getElementById("dropdown-trigger").textContent = label;
      document.getElementById("dropdown").classList.remove("open");
      renderDropdown();
      renderFolder();
    };
    list.appendChild(d);
  };
  mkItem("全部类型", "");
  dynamicTypes().forEach((t) => mkItem(t, t));
}
document.getElementById("dropdown-trigger").addEventListener("click", (e) => {
  e.stopPropagation();
  document.getElementById("dropdown").classList.toggle("open");
});
document.addEventListener("click", () => document.getElementById("dropdown").classList.remove("open"));

// 搜索
const searchInput = document.getElementById("search-input");
const searchClear = document.getElementById("search-clear");
searchInput.addEventListener("input", () => {
  searchTerm = searchInput.value.trim().toLowerCase();
  searchClear.classList.toggle("show", !!searchInput.value);
  renderFolder();
});
searchClear.addEventListener("click", () => {
  searchInput.value = ""; searchTerm = "";
  searchClear.classList.remove("show");
  renderFolder();
  searchInput.focus();
});

// 过滤：名称 / ID / 描述 / 资产路径
function matchFilter(n) {
  if (filterType && n.type !== filterType) return false;
  if (!searchTerm) return true;
  const hay = [(n.title || ""), n.id, (n.description || ""), assetPath(n), n.source || ""].join(" ").toLowerCase();
  return hay.includes(searchTerm);
}

function renderFolder() {
  const el = document.getElementById("folder");
  el.innerHTML = "";
  const kids = childrenOf(currentParent);
  const shown = kids.filter(matchFilter);
  currentIds = shown.map((k) => k.id);

  // 结果统计条 + 可点击路径
  const rc = document.getElementById("result-count");
  rc.innerHTML = "";
  if (searchTerm || filterType) {
    rc.append("匹配 ");
    const b = document.createElement("b"); b.textContent = shown.length;
    rc.appendChild(b);
    rc.append(" / 共 " + kids.length + " 项");
  } else {
    rc.append("当前共 ");
    const b = document.createElement("b"); b.textContent = kids.length;
    rc.appendChild(b);
    rc.append(" 项");
  }
  const pathSep = document.createElement("span");
  pathSep.className = "rc-path-sep"; pathSep.textContent = "—";
  rc.appendChild(pathSep);
  appendCrumb(rc);

  // 孤儿资产提示（资产根下未被任何卡片挂载引用的目录）
  const orphans = (TREE && TREE.orphans) || [];
  if (orphans.length) {
    const warn = document.createElement("span");
    warn.className = "rc-warn";
    warn.textContent = "⚠ " + orphans.length + " 个孤儿资产";
    warn.title = "未被任何卡片挂载引用：\n" + orphans.join("\n");
    warn.onclick = () => openOrphans();
    rc.appendChild(warn);
  }

  // 契约问题提示（挂载缺失 / 缺入口文档 / id 重复，由引擎 validate 给出）
  const issues = (TREE && TREE.issues) || [];
  if (issues.length) {
    const dw = document.createElement("span");
    dw.className = "rc-warn";
    dw.textContent = "⚠ " + issues.length + " 项契约问题";
    dw.title = issues.join("\n");
    dw.onclick = () => notify("契约问题：" + issues.join("；"), "warn");
    rc.appendChild(dw);
  }

  // 软建议（非问题、不拦截：未附入口文档 / 描述未结构化→降级匹配 / 段长超限 / 顶层过多建议分组）
  const hints = (TREE && TREE.hints) || [];
  if (hints.length) {
    const hb = document.createElement("span");
    hb.className = "rc-warn";
    hb.textContent = "💡 " + hints.length + " 项建议";
    hb.title = hints.join("\n");
    hb.onclick = () => notify("建议（非问题，不影响使用）：" + hints.join("；"), "info");
    rc.appendChild(hb);
  }

  if (!kids.length) {
    const e = document.createElement("div");
    e.className = "folder-empty";
    e.textContent = "此文件夹为空 · 点右下角 ＋ 新建卡片";
    el.appendChild(e); return;
  }
  if (!shown.length) {
    const e = document.createElement("div");
    e.className = "folder-empty";
    e.textContent = "无匹配结果 · 调整搜索词或类型筛选";
    el.appendChild(e); return;
  }
  shown.forEach((n, i) => el.appendChild(cardEl(n, i)));
}

function mkBtn(cls, label, title, fn) {
  const b = document.createElement("button");
  b.className = "c-btn " + cls; b.textContent = label; b.title = title;
  b.onclick = (e) => { e.stopPropagation(); fn(); };
  return b;
}

// 子树是否含基础卡片（基础卡片自身及其祖先都不能删；用后端标注的 base，不重复 @ 规则）
function subtreeHasBase(n) {
  let f = false;
  eachNode(n.children || [], (c) => { if (c.base) f = true; });
  return f;
}

function cardEl(n, index) {
  const hasKids = n.children && n.children.length;
  const isBase = !!n.base;                       // 基础卡片（名称 @ 开头；后端标注）——不可删除
  const hasBaseKid = subtreeHasBase(n);          // 子树含基础卡片 → 同样不可整体删除
  const card = document.createElement("div");
  card.className = "card-item" + (selected.has(n.id) ? " selected" : "");
  card.dataset.id = n.id;

  // === 第 1 行：#id（单击复制） + 文字按钮 ===
  const row1 = document.createElement("div"); row1.className = "c-row1";
  const idEl = document.createElement("span"); idEl.className = "c-id"; idEl.textContent = "#" + n.id;
  idEl.title = "节点 ID · 单击复制";
  idEl.onclick = (e) => {
    e.stopPropagation();
    if (navigator.clipboard) navigator.clipboard.writeText(n.id);
    notify("已复制：" + n.id, "info");
  };
  const actions = document.createElement("div"); actions.className = "c-actions";
  const delBtn = mkBtn("del", "删除",
    isBase ? "基础卡片（名称 @ 开头）不可删除"
           : hasBaseKid ? "含基础卡片，不可整体删除（先移出）"
                        : "删除该卡片（含子树）",
    () => delNode(n.id));
  delBtn.disabled = isBase || hasBaseKid;
  actions.append(delBtn, mkBtn("edit", "编辑", "编辑卡片信息", () => openEdit(n.id)));
  row1.append(idEl, actions); card.appendChild(row1);

  // === 第 2 行：图标 + 标题 ===
  const row2 = document.createElement("div"); row2.className = "c-row2";
  const icon = document.createElement("span"); icon.className = "c-icon"; icon.textContent = "💡";
  const name = document.createElement("span"); name.className = "c-name";
  name.textContent = n.title || "(未命名)"; name.title = n.title || "(未命名)";
  row2.append(icon, name); card.appendChild(row2);

  // === 第 3 行：标签胶囊（类型 + 子项数） ===
  const tags = document.createElement("div"); tags.className = "c-tags";
  const typePill = document.createElement("span");
  const tk = typeKey(n.type);
  typePill.className = "pill type-" + (["domain", "channel", "board", "methodology", "skill"].includes(tk) ? tk : "unknown");
  typePill.textContent = n.type;
  tags.appendChild(typePill);
  if (hasKids) {
    const cnt = document.createElement("span"); cnt.className = "pill count"; cnt.textContent = n.children.length + " 项";
    tags.appendChild(cnt);
  }
  card.appendChild(tags);

  // === 第 4 行：资产路径 ===
  const path = document.createElement("div"); path.className = "c-path";
  const dp = displayPath(n);
  path.textContent = dp || "（未挂载）";
  path.title = dp ? (((n.source && n.source_exists) ? "原位置：" : "位置：") + dp) : "该卡片未挂载资产";
  card.appendChild(path);

  // === 第 5 行：描述（单击弹面板；点击也选中整卡） ===
  const desc = document.createElement("div");
  desc.className = "c-desc" + (n.description ? "" : " empty");
  const txt = document.createElement("span");
  txt.className = "c-desc-text";
  txt.textContent = n.description ? ("@ " + n.description) : "@ 暂无描述，请分析并补充~";
  desc.appendChild(txt);
  desc.title = n.description || "无描述";
  desc.onclick = () => toggleDesc(n, desc);
  card.appendChild(desc);

  // 整卡点击选中（按钮已 stopPropagation）
  card.onclick = (e) => handleSelect(n.id, index, e);
  card.ondblclick = () => { if (hasKids) enterFolder(n.id); else notify("该卡片无子项", "info"); };
  return card;
}

function enterFolder(id) {
  currentParent = id; selected.clear(); lastIndex = null;
  renderFolder();
}

function handleSelect(id, index, e) {
  if (e.shiftKey && lastIndex !== null) {
    const a = Math.min(lastIndex, index), b = Math.max(lastIndex, index);
    selected.clear();
    for (let k = a; k <= b; k++) selected.add(currentIds[k]);
  } else if (e.ctrlKey || e.metaKey) {
    if (selected.has(id)) selected.delete(id); else selected.add(id);
    lastIndex = index;
  } else {
    selected.clear(); selected.add(id); lastIndex = index;
  }
  syncSelectionUI();
}
function syncSelectionUI() {
  document.querySelectorAll(".card-item").forEach((c) => {
    c.classList.toggle("selected", selected.has(c.dataset.id));
  });
}

// 描述弹层：半透明黑底白字，每 30 字一行
function toggleDesc(n, el) {
  if (descOpenId === n.id) { hideDesc(); return; }
  if (!n.description) { return; }
  const pop = document.getElementById("desc-pop");
  const lines = n.description.match(/[\s\S]{1,30}/g) || [n.description];
  pop.textContent = lines.join("\n");
  pop.classList.remove("hidden");
  const r = el.getBoundingClientRect();
  let top = r.bottom + 8;
  if (top + 120 > window.innerHeight) top = Math.max(8, r.top - 8 - pop.offsetHeight);
  pop.style.top = top + "px";
  pop.style.left = Math.min(r.left, window.innerWidth - pop.offsetWidth - 12) + "px";
  descOpenId = n.id;
}
function hideDesc() { document.getElementById("desc-pop").classList.add("hidden"); descOpenId = null; }
document.addEventListener("click", (e) => {
  if (descOpenId !== null && !e.target.closest(".c-desc") && !e.target.closest("#desc-pop")) hideDesc();
  if (!e.target.closest(".parent-pick")) hideParentDrop();
  if (!e.target.closest(".type-pick")) hideTypeDrop();
});

// ---------- 悬浮按钮 ----------
document.getElementById("fab").addEventListener("click", (e) => {
  const btn = e.target.closest(".fab"); if (!btn) return;
  const act = btn.dataset.act;
  if (act === "new") openNew();
  else if (act === "refresh") refreshTree();
  else if (act === "render") reRender();
});

// 刷新：成功 / 失败都在右上角给出通知
async function refreshTree() {
  try {
    await loadTree();
    notify("已刷新", "ok");
  } catch (err) {
    notify("刷新失败：" + err, "err");
  }
}

// ---------- 卡片操作：删除 / 编辑 / 获取 ----------

// ---------- 删除：确认弹窗（可联动删除资产目录） ----------
function delNode(id) {
  pendingDeleteId = id;
  const n = findNode(id);
  const hasMount = !!(n && n.mount);
  const wrap = document.getElementById("confirm-purge-wrap");
  wrap.classList.toggle("hidden", !hasMount);
  document.getElementById("confirm-purge").checked = false;
  document.getElementById("confirm-modal").classList.remove("hidden");
}
function closeConfirm() {
  document.getElementById("confirm-modal").classList.add("hidden");
  pendingDeleteId = null;
}
document.getElementById("confirm-ok").onclick = async () => {
  const id = pendingDeleteId;
  if (!id) return;
  const n = findNode(id);
  const keepMount = ((n && n.mount) || "").replace(/\/+$/, "");
  const purge = document.getElementById("confirm-purge").checked ? "&purge=1" : "";
  const j = await api("/api/node?id=" + encodeURIComponent(id) + purge, { method: "DELETE" });
  closeConfirm();
  if (j.ok) {
    notify("已删除：" + id, "ok");
    selected.delete(id);
    await loadTree();
    if (keepMount && ((TREE && TREE.orphans) || []).some((p) => p.replace(/\/+$/, "") === keepMount)) {
      notify("⚠ 资产目录未删除，已成为孤儿：" + keepMount + "（点顶部「⚠ 孤儿资产」处理）", "warn");
    }
  }
  else notify("删除失败：" + (j.msg || ""), "err");
};

// ---------- 孤儿资产：挂载为卡片 / 删除目录 ----------
function openOrphans() {
  const orphans = (TREE && TREE.orphans) || [];
  if (!orphans.length) { notify("没有孤儿资产", "info"); return; }
  const list = document.getElementById("orphan-list");
  list.innerHTML = "";
  orphans.forEach((p) => {
    const row = document.createElement("div"); row.className = "orphan-row";
    const path = document.createElement("div"); path.className = "orphan-path"; path.textContent = p;
    const acts = document.createElement("div"); acts.className = "orphan-acts";
    const delBtn = mkBtn("del", "删除目录", "删除该目录（需再点一次确认）", () => {
      if (delBtn.dataset.armed !== "1") {
        delBtn.dataset.armed = "1"; delBtn.textContent = "确认删除";
        setTimeout(() => {
          if (delBtn.dataset.armed === "1") { delBtn.dataset.armed = ""; delBtn.textContent = "删除目录"; }
        }, 2600);
        return;
      }
      delBtn.dataset.armed = ""; delBtn.textContent = "删除目录";
      removeOrphan(p);
    });
    acts.append(mkBtn("link", "挂载为卡片", "把该目录挂成一张新卡片（标题取目录名）", () => adoptOrphan(p)), delBtn);
    row.append(path, acts);
    list.appendChild(row);
  });
  document.getElementById("orphan-modal").classList.remove("hidden");
}
function closeOrphans() { document.getElementById("orphan-modal").classList.add("hidden"); }
async function adoptOrphan(p) {
  const id = genId();
  const name = p.replace(/\/+$/, "").split("/").pop() || id;
  const j = await api("/api/node", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ op: "add", id, type: "公共", title: name,
                           description: "暂无描述！请自行分析补全。", mount: p }),
  });
  if (j.ok) {
    notify("已挂载为卡片：" + id + issuesNote(j), (j.issues || []).length ? "warn" : "ok");
    closeOrphans(); await loadTree();
  } else notify("挂载失败：" + (j.msg || ""), "err");
}
async function removeOrphan(p) {
  const j = await api("/api/orphan?path=" + encodeURIComponent(p), { method: "DELETE" });
  if (j.ok) {
    notify("已删除孤儿目录：" + p, "ok");
    await loadTree();
    if (((TREE && TREE.orphans) || []).length) openOrphans(); else closeOrphans();
  } else notify("删除失败：" + (j.msg || ""), "err");
}

// ---------- 编辑：父级选择 ----------

// 位置候选 = 卡片定位（排除自身及子孙）；path = 卡片真实目录（目录名=卡片id，一一对应）
function positionCandidates(id) {
  const exclude = new Set([id]);
  const self = findNode(id);
  if (self) eachNode(self.children || [], (c) => exclude.add(c.id));
  const list = [{ id: "", path: rootLabel(), title: "" }];
  eachNode(TREE.nodes, (n) => {
    if (exclude.has(n.id)) return;
    list.push({ id: n.id, path: mountToDisplay(nodeMount(n)), title: n.title || "" });
  });
  return list;
}
function currentParentValue() {
  return document.getElementById("e-parent-path").value.trim();
}
function renderParentDrop(kw) {
  const drop = document.getElementById("e-parent-drop");
  drop.innerHTML = "";
  const q = (kw || "").trim().toLowerCase();
  let count = 0;
  positionCandidates(editingId).forEach((c) => {
    const label = c.title ? c.path + " ｜ " + c.title : c.path;
    if (q && !label.toLowerCase().includes(q)) return;
    count++;
    const d = document.createElement("div");
    d.className = "pd-item" + (currentParentValue() === c.path ? " active" : "");
    d.textContent = label;
    d.onclick = (e) => {
      e.stopPropagation();
      document.getElementById("e-parent-path").value = c.path;   // 卡片定位 → 落在其真实目录，归属即该卡片
      hideParentDrop();
      updateBelongHint();
    };
    drop.appendChild(d);
  });
  if (!count) {
    const e0 = document.createElement("div");
    e0.className = "pd-item none"; e0.textContent = "无匹配（可直接输入路径）";
    drop.appendChild(e0);
  }
  drop.classList.remove("hidden");
}
function hideParentDrop() { document.getElementById("e-parent-drop").classList.add("hidden"); }
const parentInput = document.getElementById("e-parent-path");
parentInput.addEventListener("click", (e) => { e.stopPropagation(); renderParentDrop(""); });
parentInput.addEventListener("focus", () => renderParentDrop(""));
parentInput.addEventListener("input", (e) => { renderParentDrop(e.target.value); updateBelongHint(); });

// 类型输入：可输入 + 可下拉选择已有类型
function renderTypeDrop(kw) {
  const drop = document.getElementById("e-type-drop");
  drop.innerHTML = "";
  const q = (kw || "").trim().toLowerCase();
  let count = 0;
  dynamicTypes().forEach((t) => {
    if (q && !t.toLowerCase().includes(q)) return;
    count++;
    const d = document.createElement("div");
    d.className = "pd-item" + (document.getElementById("e-type").value.trim() === t ? " active" : "");
    d.textContent = t;
    d.onclick = (e) => {
      e.stopPropagation();
      document.getElementById("e-type").value = t;
      hideTypeDrop();
    };
    drop.appendChild(d);
  });
  if (!count) {
    const e0 = document.createElement("div");
    e0.className = "pd-item none"; e0.textContent = "无匹配（可直接输入新类型）";
    drop.appendChild(e0);
  }
  drop.classList.remove("hidden");
}
function hideTypeDrop() { document.getElementById("e-type-drop").classList.add("hidden"); }
const typeInput = document.getElementById("e-type");
typeInput.addEventListener("click", (e) => { e.stopPropagation(); renderTypeDrop(""); });
typeInput.addEventListener("focus", () => renderTypeDrop(""));
typeInput.addEventListener("input", (e) => renderTypeDrop(e.target.value));

// ---- 归属提示：位置即归属——由位置推导最近一层卡片（唯一实现=引擎 nearest_card，经 /api/belong） ----
let belongSeq = 0;
async function updateBelongHint() {
  const el = document.getElementById("e-belong-hint");
  const seq = ++belongSeq;
  try {
    const m = normalizeMount(currentParentValue());
    const j = await api("/api/belong?mount=" + encodeURIComponent(m));
    if (seq !== belongSeq) return;                     // 快速输入：丢弃过期响应
    const id = (j && j.ok) ? j.id : "";
    el.textContent = "归属：" + (id ? pathTo(id).map((s) => s[1]).join(" / ") : rootLabel());
  } catch (e) {
    if (seq === belongSeq) el.textContent = "";
  }
}

// 选择：调起本机原生文件夹选择弹窗，初始定位到当前卡片的资产文件夹
function nodeMount(n) {
  if (!n) return "";
  return n.mount || (MOUNT_PREFIX + n.id + "/");
}
function pickBtnOf(inputId) {
  const el = document.getElementById(inputId);
  return el ? el.closest(".parent-pick").querySelector(".pick-btn") : null;
}
// 位置：目标文件夹（限制在 library 内）
async function pickFolderNative() {
  const n = findNode(editingId);
  const initial = editMode === "new" ? "" : nodeMount(n);
  const btn = pickBtnOf("e-parent-path");
  if (btn) { btn.disabled = true; btn.textContent = "选择中…"; }
  try {
    const j = await api("/api/pick-folder", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initial, mode: "dest" }),
    });
    if (j.ok && j.path) {
      document.getElementById("e-parent-path").value = mountToDisplay(j.path);
      hideParentDrop();
      updateBelongHint();
      notify("位置：" + mountToDisplay(j.path), "info");
    } else {
      notify(j.msg || "未选择文件夹", "warn");
    }
  } catch (e) {
    notify("打开文件夹对话框失败：" + e, "err");
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "选择"; }
  }
}
// 获取（复制源）：选择来源文件夹（任意本机位置；只复制，不改源）
async function pickSource() {
  const cur = document.getElementById("e-source").value.trim();
  const btn = pickBtnOf("e-source");
  if (btn) { btn.disabled = true; btn.textContent = "选择中…"; }
  try {
    const j = await api("/api/pick-folder", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initial: cur, mode: "source" }),
    });
    if (j.ok && j.path) {
      document.getElementById("e-source").value = j.path;
      notify("已获取：" + j.path, "info");
    } else {
      notify(j.msg || "未选择文件夹", "warn");
    }
  } catch (e) {
    notify("打开文件夹对话框失败：" + e, "err");
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "选择"; }
  }
}

// 挂载路径 → 展示格式（主页(assets)/子目录…）
function mountToDisplay(mount) {
  let v = String(mount || "");
  if (v.startsWith(MOUNT_PREFIX)) v = v.slice(MOUNT_PREFIX.length);
  v = v.replace(/^\/+/, "").replace(/\/+$/, "");
  return v ? rootLabel() + "/" + v : rootLabel();
}
// 挂载路径 → 本机完整路径（绝对路径）
function absPathOf(mount) {
  const root = (REPO_ROOT_ABS || "").replace(/[\\/]+$/, "");
  const rel = String(mount || "").replace(/^\/+/, "").replace(/\/+$/, "");
  if (!root) return rel;
  const sep = root.includes("\\") ? "\\" : "/";
  return root + sep + rel.split("/").join(sep);
}
// 输入值 → 挂载路径（统一落在资产根内）。接受的三种写法：
//   展示格式「主页(assets)/子目录」、本机绝对路径、资产根下的相对路径
function normalizeMount(val) {
  let v = String(val || "").replace(/\\/g, "/").trim().replace(/\/+$/, "");
  if (!v) return "";
  const root = rootLabel();                                  // 主页(assets)
  if (v === root) return MOUNT_PREFIX;
  if (v.startsWith(root + "/")) return MOUNT_PREFIX + v.slice(root.length + 1) + "/";
  const abs = String(REPO_ROOT_ABS || "").replace(/\\/g, "/").replace(/\/+$/, "");
  if (abs && v.startsWith(abs + "/")) v = v.slice(abs.length + 1);   // 本机绝对路径 → 相对项目根
  if (v.startsWith(MOUNT_PREFIX)) return v + "/";
  return MOUNT_PREFIX + v.replace(/^\/+/, "") + "/";          // 其余视为资产根下的相对路径
}

// ---------- 编辑 / 新增弹窗 ----------
function openNew() {
  editMode = "new";
  editingId = null;
  document.getElementById("edit-title").textContent = "新增";
  document.getElementById("e-save").textContent = "确认";
  const src = document.getElementById("e-source");
  src.value = "";
  // 位置默认 = 当前所在文件夹（在哪添加就归到哪）；归属由位置推导（保存时引擎落定）
  document.getElementById("e-parent-path").value =
    mountToDisplay(currentParent ? nodeMount(findNode(currentParent)) : MOUNT_PREFIX);
  document.getElementById("e-type").value = "公共";
  const t = document.getElementById("e-title");
  t.value = ""; t.placeholder = "留空则自动使用复制源文件夹名";
  const d = document.getElementById("e-desc");
  d.value = ""; d.placeholder = "暂无描述！请自行分析补全。";
  hideParentDrop(); hideTypeDrop();
  updateBelongHint();
  document.getElementById("edit-modal").classList.remove("hidden");
}

async function openEdit(id) {
  const n = findNode(id); if (!n) return;
  editMode = "edit";
  editingId = id;
  editOriginalMount = n.mount || "";
  document.getElementById("edit-title").textContent = "编辑卡片";
  document.getElementById("e-save").textContent = "保存";
  // 获取（复制源）：显示本机完整路径；打开时探测，源不存在 → 自动指向本卡片当前资产目录
  const srcEl = document.getElementById("e-source");
  let srcVal = n.source || "";
  let srcOk = !!n.source_exists;
  if (srcVal) {
    try {
      const p = await api("/api/exists?path=" + encodeURIComponent(srcVal));
      srcOk = !!(p && p.ok && p.exists);
    } catch (e) { /* 探测失败：按最近一次加载的标注 */ }
  }
  if (!srcVal || !srcOk) {
    const m = n.mount || "";
    if (m) srcVal = absPathOf(m);
  }
  srcEl.value = srcVal;
  editBaseMount = positionOf(n);
  document.getElementById("e-parent-path").value = mountToDisplay(editBaseMount);
  document.getElementById("e-type").value = n.type || "";
  const t = document.getElementById("e-title");
  t.value = n.title || ""; t.placeholder = "卡片名称";
  const d = document.getElementById("e-desc");
  d.value = n.description || ""; d.placeholder = "这个卡片是做什么的…";
  hideParentDrop(); hideTypeDrop();
  updateBelongHint();
  document.getElementById("edit-modal").classList.remove("hidden");
}
function closeEdit() {
  document.getElementById("edit-modal").classList.add("hidden");
  hideParentDrop(); hideTypeDrop();
  editingId = null;
  editMode = "edit";
}

// 卡片当前"位置"（父目录，不含卡片 id）；找不到 id 后缀则返回其挂载本身
function positionOf(n) {
  const m = nodeMount(n).replace(/\/+$/, "");
  if (m && m.endsWith("/" + n.id)) return m.slice(0, -(n.id.length + 1)) + "/";
  return m ? m + "/" : "";
}
// 计算目标挂载目录 = 位置 + 卡片id（id 即资产文件夹名，天然唯一）
function destMount(id) {
  let base = normalizeMount(document.getElementById("e-parent-path").value.trim());
  if (!base) base = MOUNT_PREFIX;                    // 位置留空 = 默认主页
  // 编辑且位置未改动 → 保持原挂载不动（幂等）
  if (editMode === "edit" && base === editBaseMount && editOriginalMount) return editOriginalMount;
  const b = base.replace(/\/+$/, "");
  if (!id) return b ? b + "/" : base;
  if (b.endsWith("/" + id)) return b + "/";          // 已含 id，避免重复
  return b + "/" + id + "/";
}

async function saveEdit() {
  const type = document.getElementById("e-type").value.trim();
  const title = document.getElementById("e-title").value.trim();
  const description = document.getElementById("e-desc").value.trim();
  const source = document.getElementById("e-source").value.trim();
  const srcName = source ? source.replace(/\\/g, "/").replace(/\/+$/, "").split("/").pop() : "";

  if (editMode === "new") {
    if (!source) { notify("请先获取复制源（必填）", "warn"); return; }
    const id = genId();
    const mount = destMount(id);
    const j = await api("/api/node", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        op: "add", id,
        type: type || "公共",
        title: title || srcName || "待命名",
        description: description || "暂无描述！请自行分析补全。",
        source,
        mount,
      }),
    });
    if (j.ok) {
      notify("已新增：" + id + issuesNote(j), (j.issues || []).length ? "warn" : "ok");
      closeEdit(); selected.clear(); selected.add(id); await loadTree();
    } else notify("新增失败：" + (j.msg || ""), "err");
    return;
  }

  const id = editingId; if (!id) return;
  const mount = destMount(id);
  const j = await api("/api/node", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      op: "update", id, title, description, type, source, mount,
    }),
  });
  if (j.ok) { notify("已保存" + issuesNote(j), (j.issues || []).length ? "warn" : "ok"); closeEdit(); selected.clear(); await loadTree(); }
  else notify("保存失败：" + (j.msg || ""), "err");
}

// ---------- 公共 ----------
async function reRender() {
  try {
    const j = await api("/api/render", { method: "POST" });
    if (j.ok) notify("ROUTES.md 已重绘" + issuesNote(j), (j.issues || []).length ? "warn" : "ok");
    else notify("重绘失败：" + (j.msg || ""), "err");
  } catch (err) {
    notify("重绘失败：" + err, "err");
  }
}
// 写操作返回的契约问题清单 → 提示后缀
function issuesNote(j) {
  const d = (j && j.issues) || [];
  return d.length ? "（⚠ " + d.length + " 项契约问题）" : "";
}
loadTree().catch((err) => notify("加载失败：" + err, "err"));

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
乐药云智库 API 完整封装 v1.0（AI 友好 CLI / 可 import）
========================================================
覆盖两大阶段：

阶段1 · 登录与凭证
  status                    检查当前 token 是否有效
  login                     生成二维码 → 用户扫码 → 自动轮询 → 换 token → 自动关码并保存凭证
  （所有阶段2命令会自动判断：token 失效 → 自动回到阶段1重登 → 重试原命令一次）

阶段2 · 数据操作（全部支持并发，--workers 上限 50）
  search       关键字搜索（含分页）
  categories   获取栏目/搜索目录
  children     获取目录子项（可递归）
  summary      获取目录中特定结果的摘要/介绍
  detail       获取完整页面内容：标题/正文/附件/子项
  next / prev / jump   翻页（下一页/上一页/跳转指定页）
  download     下载单个附件（PDF/PPT/图片等）
  get-attachments     批量并发下载某文档全部附件
  collect      并发全库采集：搜索全部页 + 抓全部详情 +（可选）下载附件

输出规范（AI 友好）：
  * 所有命令输出严格 JSON；--compact 输出单行便于机器解析
  * 退出码：0=成功 1=业务错误 2=参数错误 3=需要登录 4=网络/超时
  * 凭证自动持久化到 token 文件，后续命令自动复用

用法示例：
  python leyou_cloud.py status
  python leyou_cloud.py search 毛利 --page 1 --pagesize 10
  python leyou_cloud.py detail <slug> --html
  python leyou_cloud.py collect 毛利 --workers 12 --download
  python leyou_cloud.py get-attachments <slug> --dir ./files --workers 8
"""
import argparse
import json
import os
import re
import sys
import math
import time
import io as _io
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.adapters import HTTPAdapter

# ---------------- 常量（站点级，勿改） ----------------
TENANT_ID     = "8980"
SITE          = "https://leyohrai.helplook.net"
BASE_GET      = "https://api-get.helplook.net"          # 只读数据域名
BASE_API      = "https://api.helplook.net"              # 写操作 / AI 域名
CALLBACK      = "https://api-sh.helplook.net/oauth/callback-wechat-work-oauth/customer-auth-login"
POLL_URL      = "https://login.work.weixin.qq.com/wwlogin/monoApi/sso/login/getWebQrCodeStatus"
QR_IMG_URL    = "https://login.work.weixin.qq.com/wwlogin/sso/qrcode"
DEFAULT_UUID  = "iKMAE54kytaWpL7Z401jh"                 # 搜索需要，浏览器 cookie hl_uuid
DEFAULT_TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "leyou_token.json")
MAX_WORKERS   = 50                                       # 并发硬上限（用户要求 50）
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# 登录弹窗窗口图标（用户提供的 SVG，笔记本+对勾）；自动渲染
_ICON_SVG = '''<svg t="1787214001400" class="icon" viewBox="0 0 1024 1024" version="1.1" xmlns="http://www.w3.org/2000/svg" p-id="8003" width="200" height="200"><path d="M952.63097563 251.5136V725.12H80.37497562V251.5136A87.4944 87.4944 0 0 1 167.15897563 156.8H865.46297563a87.9168 87.9168 0 0 1 87.168 94.7136z m-446.40000001 191.712s-18.24-63.7248-122.496-93.0432a139.9872 139.9872 0 0 0-2.112 274.0224h0.384l257.664-0.5568h15.55200001a123.3984 123.3984 0 0 0 95.424-124.0512 125.5872 125.5872 0 0 0-76.80000001-118.4064 158.208 158.208 0 0 0-151.104-120.6528 152.7744 152.7744 0 0 0-125.56799999 67.6608c51.84 7.8912 102.72 36.0768 109.056 115.0272z" fill="#F49248" p-id="8004"></path><path d="M32.18297562 819.8528a43.3728 43.3728 0 0 0 42.62400001 47.3664h873.6a47.52 47.52 0 0 0 0-94.7328H74.80697563A43.3728 43.3728 0 0 0 32.18297562 819.8528z m389.95200001-29.8752h174.336a23.7504 23.7504 0 0 1-1e-8 47.3472h-174.336a23.7504 23.7504 0 0 1 1e-8-47.3472z" fill="#F6B381" p-id="8005"></path></svg>'''
_ICON_PNG_CACHE = None   # 首次渲染后缓存 PNG bytes，避免每次弹窗重复扫描线填充

# ---------------- 异常 ----------------
class TokenExpired(Exception):
    """token 失效（服务端返回 507）"""
class LoginRequired(Exception):
    """需要登录但被禁止自动扫码"""

# ---------------- 工具函数 ----------------
def _plain(html):
    """HTML -> 纯文本（去标签、压缩空白）"""
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()

def _kind_of(url):
    """按扩展名判断附件类型"""
    ext = (url.split("?")[0].split("#")[0].split(".")[-1] or "").lower()
    if ext in ("pdf",):                 return "pdf"
    if ext in ("ppt", "pptx", "pps"):   return "ppt"
    if ext in ("png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "ico"): return "image"
    if ext in ("doc", "docx", "xls", "xlsx", "csv", "txt", "md"):         return "doc"
    if ext in ("zip", "rar", "7z"):     return "archive"
    return "other"

def _extract_resources(html):
    """从正文 HTML 提取全部资源直链（附件 a 标签 + iframe/embed/object 内嵌 PDF/PPT）"""
    out, seen = [], set()
    if not html:
        return out
    patterns = [
        r"<a[^>]+href=['\"]([^'\"]+)['\"][^>]*>([^<]*)</a>",
        r"<iframe[^>]+src=['\"]([^'\"]+)['\"]",
        r"<embed[^>]+src=['\"]([^'\"]+)['\"]",
        r"<object[^>]+data=['\"]([^'\"]+)['\"]",
    ]
    for pat in patterns:
        for m in re.finditer(pat, html, re.I):
            url = m.group(1)
            if url.startswith("//"):
                url = "https:" + url
            if url not in seen:
                seen.add(url)
                name = ""
                if len(m.groups()) > 1 and m.group(2):
                    name = m.group(2).strip()
                if "resource-wangsu" in url or "helplook.net" in url or name:
                    out.append({"name": name or os.path.basename(url.split("?")[0]) or url,
                                "url": url, "kind": _kind_of(url)})
    # 仅保留资源直链（去锚点/去页面链接噪声）
    return [r for r in out if "resource-wangsu" in r["url"] or r["kind"] in ("pdf", "ppt")]

def _emit(out, compact=False):
    s = json.dumps(out, ensure_ascii=False,
                   separators=(",", ":") if compact else None,
                   indent=None if compact else 2)
    print(s)


# ---------------- SVG 图标渲染（自研，零额外依赖） ----------------
# 轻量 SVG path 解析 + 扫描线 even-odd 填充 + 4x 超采样抗锯齿
# 支持命令：M/L/H/V/C/S/Q/T/A/Z 及小写相对坐标
def _render_svg_to_png(svg, size=64):
    """渲染 SVG（仅 path+fill）→ PNG bytes。失败返回 None。"""
    global _ICON_PNG_CACHE
    if _ICON_PNG_CACHE is not None and len(_ICON_PNG_CACHE) > 0:
        return _ICON_PNG_CACHE
    try:
        from PIL import Image
    except Exception:
        return None
    ARITY = {'M': 2, 'L': 2, 'H': 1, 'V': 1, 'C': 6, 'S': 4, 'Q': 4, 'T': 2, 'A': 7, 'Z': 0}
    NUM = r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?'

    def parse_path(d):
        toks = re.findall(r'[A-Za-z]|' + NUM, d)
        cmds, i, prev = [], 0, None
        while i < len(toks):
            if re.fullmatch(r'[A-Za-z]', toks[i]):
                cmd, i = toks[i], i + 1
            else:
                cmd = prev or 'L'
            prev = cmd
            if cmd in 'Zz':
                cmds.append((cmd, []))
                continue
            n = ARITY[cmd.upper()]
            params = []
            while i < len(toks) and re.fullmatch(NUM, toks[i]):
                params.append(float(toks[i])); i += 1
            for k in range(0, len(params) - len(params) % n, n):
                cmds.append((cmd, params[k:k + n]))
        return cmds

    def sample_cubic(p0, p1, p2, p3, steps=14):
        return [((1-t)**3*p0[0]+3*(1-t)**2*t*p1[0]+3*(1-t)*t*t*p2[0]+t**3*p3[0],
                 (1-t)**3*p0[1]+3*(1-t)**2*t*p1[1]+3*(1-t)*t*t*p2[1]+t**3*p3[1])
                for t in (i/steps for i in range(steps + 1))]
    def sample_quad(p0, p1, p2, steps=12):
        return [(1-t)**2*p0[0]+2*(1-t)*t*p1[0]+t*t*p2[0],
                (1-t)**2*p0[1]+2*(1-t)*t*p1[1]+t*t*p2[1]]
    def arc_to_points(p0, rx, ry, rot, large, sweep, p1):
        if abs(p0[0]-p1[0]) < 1e-9 and abs(p0[1]-p1[1]) < 1e-9: return []
        rx, ry = abs(rx), abs(ry)
        phi = math.radians(rot); cp, sp = math.cos(phi), math.sin(phi)
        x1p = cp*(p0[0]-p1[0])/2 + sp*(p0[1]-p1[1])/2
        y1p = -sp*(p0[0]-p1[0])/2 + cp*(p0[1]-p1[1])/2
        lam = x1p*x1p/(rx*rx) + y1p*y1p/(ry*ry)
        if lam > 1:
            s = math.sqrt(lam); rx *= s; ry *= s
        num = rx*rx*ry*ry - rx*rx*y1p*y1p - ry*ry*x1p*x1p
        den = rx*rx*y1p*y1p + ry*ry*x1p*x1p
        coef = math.sqrt(max(0.0, num/den)) if den else 0
        if large == sweep: coef = -coef
        cxp = coef*rx*y1p/ry; cyp = coef*-ry*x1p/rx
        cx = cp*cxp - sp*cyp + (p0[0]+p1[0])/2
        cy = sp*cxp + cp*cyp + (p0[1]+p1[1])/2
        ux, uy = (x1p-cxp)/rx, (y1p-cyp)/ry
        vx, vy = (-x1p-cxp)/rx, (-y1p-cyp)/ry
        a1 = math.atan2(uy, ux)
        da = math.atan2(vy, vx) - a1
        if not sweep and da > 0: da -= 2*math.pi
        if sweep and da < 0: da += 2*math.pi
        steps = max(6, int(abs(da) / (math.pi/24)))
        pts = []
        for i in range(steps + 1):
            a = a1 + da * i / steps
            x = cx + rx*math.cos(a)*cp - ry*math.sin(a)*sp
            y = cy + rx*math.cos(a)*sp + ry*math.sin(a)*cp
            pts.append((x, y))
        return pts

    def path_to_contours(d):
        cmds = parse_path(d)
        contours, sub = [], []
        cur = [0.0, 0.0]; start = [0.0, 0.0]
        lc, lq = None, None
        for cmd, p in cmds:
            c, rel = cmd.upper(), (cmd.islower() and cmd.upper() != 'Z')
            if c == 'M':
                if sub: contours.append(list(sub)); sub = []
                s = [cur[0]+p[0] if rel else p[0], cur[1]+p[1] if rel else p[1]]
                start, cur, sub = list(s), list(s), [tuple(s)]; lc = lq = None
            elif c == 'L':
                for i in range(0, len(p), 2):
                    cur = [cur[0]+p[i] if rel else p[i], cur[1]+p[i+1] if rel else p[i+1]]
                    sub.append(tuple(cur))
                lc = lq = None
            elif c == 'H':
                for v in p:
                    cur = [cur[0]+v if rel else v, cur[1]]; sub.append(tuple(cur))
                lc = lq = None
            elif c == 'V':
                for v in p:
                    cur = [cur[0], cur[1]+v if rel else v]; sub.append(tuple(cur))
                lc = lq = None
            elif c == 'C':
                for i in range(0, len(p), 6):
                    p0 = tuple(cur)
                    p1 = (cur[0]+p[i] if rel else p[i], cur[1]+p[i+1] if rel else p[i+1])
                    p2 = (cur[0]+p[i+2] if rel else p[i+2], cur[1]+p[i+3] if rel else p[i+3])
                    p3 = (cur[0]+p[i+4] if rel else p[i+4], cur[1]+p[i+5] if rel else p[i+5])
                    sub.extend(sample_cubic(p0, p1, p2, p3)[1:]); cur = list(p3); lc = p2
                lq = None
            elif c == 'S':
                for i in range(0, len(p), 4):
                    p0 = tuple(cur)
                    p1 = (2*cur[0]-lc[0], 2*cur[1]-lc[1]) if lc else p0
                    p2 = (cur[0]+p[i] if rel else p[i], cur[1]+p[i+1] if rel else p[i+1])
                    p3 = (cur[0]+p[i+2] if rel else p[i+2], cur[1]+p[i+3] if rel else p[i+3])
                    sub.extend(sample_cubic(p0, p1, p2, p3)[1:]); cur = list(p3); lc = p2
                lq = None
            elif c == 'Q':
                for i in range(0, len(p), 4):
                    p0 = tuple(cur)
                    p1 = (cur[0]+p[i] if rel else p[i], cur[1]+p[i+1] if rel else p[i+1])
                    p2 = (cur[0]+p[i+2] if rel else p[i+2], cur[1]+p[i+3] if rel else p[i+3])
                    sub.extend(sample_quad(p0, p1, p2)[1:]); cur = list(p2); lq = p1
                lc = None
            elif c == 'T':
                for i in range(0, len(p), 2):
                    p0 = tuple(cur)
                    p1 = (2*cur[0]-lq[0], 2*cur[1]-lq[1]) if lq else p0
                    p2 = (cur[0]+p[i] if rel else p[i], cur[1]+p[i+1] if rel else p[i+1])
                    sub.extend(sample_quad(p0, p1, p2)[:]); cur = list(p2); lq = p1
                lc = None
            elif c == 'A':
                for i in range(0, len(p), 7):
                    rx, ry, rot = p[i], p[i+1], p[i+2]
                    large, sweep = int(p[i+3]), int(p[i+4])
                    x2 = cur[0]+p[i+5] if rel else p[i+5]
                    y2 = cur[1]+p[i+6] if rel else p[i+6]
                    pts = arc_to_points(tuple(cur), rx, ry, rot, large, sweep, (x2, y2))
                    if pts: sub.extend(pts[1:])
                    cur = [x2, y2]
                lc = lq = None
            elif c == 'Z':
                cur = list(start); lc = lq = None
        if sub: contours.append(sub)
        return contours

    big = size * 4   # 4x 超采样抗锯齿
    big_imgs = []
    for m in re.finditer(r'<path\b([^>]*)>', svg):
        attrs = m.group(1)
        fd = re.search(r'\bd="([^"]*)"', attrs)
        ff = re.search(r'\bfill="([^"]*)"', attrs)
        if not fd: continue
        col = ff.group(1) if ff else "#000000"
        if col.startswith('#') and len(col) == 7:
            col = (int(col[1:3], 16), int(col[3:5], 16), int(col[5:7], 16), 255)
        else:
            col = (0, 0, 0, 255)
        cs = path_to_contours(fd.group(1))
        if not cs: continue
        img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        px = img.load(); scale = big / 1024.0
        scaled = [[(x*scale, y*scale) for (x, y) in c] for c in cs]
        for y in range(big):
            yy = y + 0.5
            xs = []
            for cont in scaled:
                n = len(cont)
                for i in range(n):
                    x1, y1 = cont[i]; x2, y2 = cont[(i+1) % n]
                    if (y1 <= yy < y2) or (y2 <= yy < y1):
                        xs.append(x1 + (yy - y1) * (x2 - x1) / (y2 - y1))
            xs.sort()
            for k in range(0, len(xs) - 1, 2):
                xa, xb = xs[k], xs[k+1]
                x0 = max(0, int(math.ceil(xa))); x1b = min(big, int(xb))
                for x in range(x0, x1b):
                    px[x, y] = col
        big_imgs.append(img)
    if not big_imgs: return None
    canvas = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    for im in big_imgs:
        canvas.alpha_composite(im)
    canvas = canvas.resize((size, size), Image.LANCZOS)
    buf = _io.BytesIO()
    canvas.save(buf, "PNG")
    _ICON_PNG_CACHE = buf.getvalue()
    return _ICON_PNG_CACHE


def _make_tick_png(size=140):
    """生成「绿圆 + 白勾」透明 PNG bytes。

    用于扫码成功后在二维码上叠加显示。采用 Image（而非 Canvas 线条/文本），
    彻底规避 tkinter 字体缺失、同 Canvas 中 stipple 矩形覆盖线段、z-order 等
    渲染问题——create_image 的 image 永远绘制在 line/rect 之上，100% 可见。"""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = int(size * 0.06)
    d.ellipse([m, m, size - m, size - m], fill="#00A651")
    # 白色对勾（两段粗线，圆头）
    lw = max(6, int(size * 0.085))
    p1 = (size * 0.30, size * 0.525)
    p2 = (size * 0.435, size * 0.66)
    p3 = (size * 0.72, size * 0.345)
    for a, b in ((p1, p2), (p2, p3)):
        d.line([(a[0], a[1]), (b[0], b[1])], fill="white", width=lw, joint="curve")
    buf = _io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()

# ---------------- 凭证存储 ----------------
def _load_token_file(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_token_file(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ---------------- 主封装类 ----------------
class LeyouCloud:
    def __init__(self, token=None, uuid=None, token_file=DEFAULT_TOKEN_FILE,
                 workers=10, timeout=20, auto_login=True):
        self.token_file = token_file
        self.workers = max(1, min(int(workers or 10), MAX_WORKERS))
        self.timeout = timeout
        self.auto_login = auto_login

        store = _load_token_file(token_file)
        self.token = token or store.get("token")
        self.uuid = uuid or store.get("uuid") or DEFAULT_UUID
        self.login_at = store.get("login_at")
        self.expires_at = store.get("expires_at")
        self.watermark = store.get("watermark")

        self.session = self._new_session()

    # ---------- 会话 ----------
    def _new_session(self):
        s = requests.Session()
        s.headers.update({
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": SITE + "/",
        })
        if self.token:
            s.headers["x-auth-token"] = self.token
        # 连接池调大以支撑并发（上限 50 时留余量）
        ad = HTTPAdapter(pool_connections=100, pool_maxsize=100, max_retries=0)
        s.mount("https://", ad)
        s.mount("http://", ad)
        return s

    def _request(self, url, params=None, headers=None, stream=False, timeout=None):
        """带重试的 GET；业务码 507 抛 TokenExpired"""
        t = timeout or self.timeout
        last = None
        for i in range(3):
            try:
                r = self.session.get(url, params=params, headers=headers,
                                     stream=stream, timeout=t)
                # 解析业务码
                if not stream and "application/json" in (r.headers.get("Content-Type", "") or "") or (r.text or "").lstrip().startswith("{"):
                    try:
                        j = r.json()
                        if isinstance(j, dict) and j.get("code") == 507:
                            raise TokenExpired(j.get("msg", "Invalid token"))
                    except (ValueError, requests.exceptions.JSONDecodeError):
                        pass
                return r
            except TokenExpired:
                raise
            except requests.exceptions.RequestException as e:
                last = e
                time.sleep(0.5 * (i + 1))
        raise last or requests.exceptions.ConnectionError("请求失败")

    def _get(self, url, params=None, headers=None, stream=False):
        return self._request(url, params, headers, stream)

    def _save(self):
        _save_token_file(self.token_file, {
            "token": self.token, "uuid": self.uuid,
            "login_at": self.login_at, "expires_at": self.expires_at,
            "watermark": self.watermark,
        })

    # ================= 阶段 1：登录 / 凭证 =================
    def check_login(self):
        """token 有效性检测：get-list 带 token 200 / 无 token 507（实测确认）"""
        if not self.token:
            return False, {}
        try:
            r = self._get(f"{BASE_GET}/foreground/content/get-list",
                          {"tannant_id": TENANT_ID, "data_type": 1})
            j = r.json()
            lst = (j.get("data") or {}).get("list") or []
            if j.get("code") == 200 and lst:
                return True, {"categories": len(lst),
                              "first_category": lst[0].get("name", "")}
            return False, {}
        except TokenExpired:
            return False, {}
        except Exception:
            return False, {}

    def login(self, qr_out="qrcode.png", max_wait=300, quiet=False, popup=True):
        """完整扫码登录：取登录 URL → 提取 key → 下载二维码 →（默认弹窗）轮询
        auth_code → 直接拼回调换 token → 保存凭证 → 自动关闭并删除二维码。
        返回用户登录信息 dict。"""
        if not quiet:
            _emit({"ok": True, "step": "get-auth-url", "message": "获取企业微信登录地址..."})
        r = self._get(f"{BASE_GET}/foreground/tannant/get-auth-url",
                      {"tannant_id": TENANT_ID, "redirect_uri": SITE + "/"})
        login_url = r.json()["data"]["url"]

        hdr = {"User-Agent": UA}
        html = requests.get(login_url, headers=hdr, timeout=self.timeout).text
        m_key = re.search(r"qrcode\?key=([0-9a-f]+)", html)
        m_sig = re.search(r'sessionSignature\\?"\s*[:=]\s*"(Bearer [^"]+)"', html) or \
                re.search(r"sessionSignature\":\"(Bearer [^\"]+)\"", html)
        if not m_key or not m_sig:
            raise RuntimeError("无法从登录页提取 key/sessionSignature，请重试")
        key, sig = m_key.group(1), m_sig.group(1)

        qr = requests.get(f"{QR_IMG_URL}?key={key}", headers=hdr, timeout=self.timeout)
        with open(qr_out, "wb") as f:
            f.write(qr.content)

        state = (f'{{"tannant_id":"{TENANT_ID}","source":"work_wechat",'
                 f'"redirect_uri":"{SITE.replace("https://", "https:\\\\/\\\\/")}\\\\/"}}')
        if popup:
            # 默认：弹出 tkinter 二维码窗口，扫码成功/超时/取消自动关闭
            try:
                token = self._login_popup(qr_out, key, sig, state, login_url, hdr, max_wait)
            except Exception as e:
                if not quiet:
                    _emit({"ok": False, "warn": "POPUP_UNAVAILABLE",
                           "message": f"二维码弹窗不可用（{e}），降级为图片模式：{os.path.abspath(qr_out)}"})
                token = self._poll_and_exchange(key, sig, state, login_url, hdr, max_wait)
        else:
            if not quiet:
                _emit({"ok": True, "step": "qrcode", "qr_file": os.path.abspath(qr_out),
                       "message": f"二维码已生成：{qr_out}，请用企业微信 App 扫码并确认（{max_wait}s 内）"})
            token = self._poll_and_exchange(key, sig, state, login_url, hdr, max_wait)
        if not token:
            raise RuntimeError("等待扫码超时，请重新执行 login 并尽快扫码")

        self.token = token
        self.session.headers["x-auth-token"] = token
        # 提取 uuid（登录后 cookie hl_uuid，取不到用默认）
        cj = self.session.cookies.get_dict()
        self.uuid = cj.get("hl_uuid") or self.uuid or DEFAULT_UUID
        # 获取水印（品牌信息）
        try:
            w = self._get(f"{BASE_GET}/foreground/tannant/get-watermark",
                          {"tannant_id": TENANT_ID}).json()
            self.watermark = (w.get("data") or {}).get("text", "")
        except Exception:
            self.watermark = ""
        self.login_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self.expires_at = time.strftime("%Y-%m-%d", time.localtime(time.time() + 30 * 86400))
        self._save()
        # 登录成功自动关闭二维码（删除本地图片）
        try:
            if os.path.exists(qr_out):
                os.remove(qr_out)
        except Exception:
            pass
        return self.whoami()

    def _poll_and_exchange(self, key, sig, state, login_url, hdr, max_wait,
                           cancel=None, on_status=None):
        """轮询企业微信扫码状态 → 拿到 auth_code → 立即拼回调换 token。返回 token 或 None。
        on_status(status)：每次扫码状态变化时回调（如 QRCODE_SCAN_ING=已扫码待确认）。"""
        h_poll = {**hdr,
                  "authorization": sig,
                  "x-wecom-client": f"ww-sso-login:{int(time.time()*1000)}:master",
                  "Content-Type": "application/json",
                  "Referer": login_url}
        last, open_sid = "QRCODE_SCAN_NEVER", None
        start = time.time()
        while time.time() - start < max_wait:
            if cancel and cancel["v"]:
                return None
            try:
                rr = self.session.post(
                    POLL_URL,
                    params={"lang": "zh_CN", "ajax": 1, "f": "json",
                            "random": str(int(time.time() * 1000))[-6:]},
                    json={"webKey": key, "lastStatus": last, "openDataSid": open_sid},
                    headers=h_poll, timeout=15)
                d = (rr.json() or {}).get("data", {}) or {}
                status = d.get("status")
                if status and status != last:
                    if on_status:
                        try:
                            on_status(status)
                        except Exception:
                            pass
                    last = status
                if d.get("openDataSid"):
                    open_sid = d["openDataSid"]
                ac = d.get("auth_code")
                if ac:   # 关键：auth_code 就是 OAuth code，立即拼回调
                    # 保险：确保 GUI 层显示"扫码成功"（极快确认时可能跳过 ING 状态）
                    if on_status:
                        try:
                            on_status("QRCODE_SCAN_SUCC")
                        except Exception:
                            pass
                    res = self.session.get(CALLBACK, params={"code": ac, "state": state},
                                           headers={"User-Agent": hdr["User-Agent"]},
                                           allow_redirects=True, timeout=20)
                    m = re.search(r"[?&]token=([0-9A-Za-z_.\-]+)", res.url)
                    if m:
                        return m.group(1)
                if status == "QRCODE_SCAN_ERR":
                    raise RuntimeError("二维码已过期或已取消，请重新执行 login")
            except RuntimeError:
                raise
            except requests.exceptions.RequestException:
                pass   # 轮询偶发超时（企业微信限流）忽略继续
            time.sleep(0.8)   # 0.8s 轮询，避免错过短暂的"已扫码待确认"状态
        return None

    def _login_popup(self, qr_path, key, sig, state, login_url, hdr, max_wait):
        """弹出 tkinter 二维码窗口（标准库，零依赖）。

        界面（极简）：
          [二维码区域]             ← 扫码成功后盖灰色蒙版 + 中间绿色 √
          请使用企微扫码【剩余Ns】…  ← 扫码成功后变为「扫码成功！请确认~」

        交互：
          扫码成功（企微已扫待确认）→ 蒙版 + 绿√ + 提示切换；
          确认登录成功（换到 token）→ 窗口自动关闭；
          超时 / 点右上角关闭        → 窗口自动关闭并抛对应错误。

        线程安全：worker 轮询线程只向 queue 投递事件，
        GUI 线程在 after 循环中消费，所有 tkinter 操作都在 GUI 线程完成。"""
        import queue as _queue
        import threading as _th
        import tkinter as tk

        root = tk.Tk()
        root.title("乐药云智库 · 扫码登录")
        root.attributes("-topmost", True)
        root.resizable(False, False)

        # 自定义窗口图标（SVG 渲染 → PNG，笔记本+对勾）；失败降级为纯色块
        try:
            png = _render_svg_to_png(_ICON_SVG, size=64)
            if png:
                icon = tk.PhotoImage(data=png)
            else:
                raise RuntimeError("svg render failed")
        except Exception:
            icon = tk.PhotoImage(width=32, height=32)
            icon.put("#E79907", to=(0, 0, 32, 32))
            for r in range(8):
                for c in range(8):
                    icon.put("#FFFFFF", to=(12 + c, 12 + r, 13 + c, 13 + r))
        root.iconphoto(True, icon)
        root._leyou_icon = icon  # 保持引用防 GC

        # 二维码区域（Canvas，扫码后盖半透明黑蒙版 + 绿√在最上层）
        QR_DISPLAY = 220          # 二维码显示宽度上限（px），弹窗更紧凑
        img_raw = tk.PhotoImage(file=qr_path)
        scale = max(1, img_raw.width() // QR_DISPLAY)
        img = img_raw.subsample(scale, scale) if scale > 1 else img_raw
        w, h = img.width(), img.height()
        canvas = tk.Canvas(root, width=w, height=h, highlightthickness=0,
                           bg="#FFFFFF")
        canvas.pack(padx=12, pady=(12, 6))
        canvas.create_image(w // 2, h // 2, image=img)
        # 半透明黑色蒙版（fill 黑色 + gray50 点阵 = ~50% 黑半透明感）
        mask = canvas.create_rectangle(0, 0, w, h, fill="#000000",
                                       stipple="gray50", outline="", state="hidden")
        # 绿圆 + 白勾（PIL 生成的透明 PNG，create_image 永远画在 mask 之上）
        # 直接按目标尺寸生成，避免 subsample 精度问题
        ts = max(48, int(min(w, h) * 0.42))
        tick_png = _make_tick_png(ts)
        if tick_png:
            tick_img = tk.PhotoImage(data=tick_png)
            root._leyou_tick = tick_img   # 保持引用防 GC
            tick_item = canvas.create_image(w // 2, h // 2, image=tick_img,
                                            state="hidden")
        else:
            tick_item = None   # 极端情况：PIL 不可用（极少触发）

        # 提示行（含倒计时）
        tip = tk.Label(root, text="", font=("Microsoft YaHei UI", 10))
        tip.pack(pady=(0, 10))

        evt_q = _queue.Queue()
        result = {"token": None, "error": None}
        cancel = {"v": False}
        deadline = time.time() + max_wait
        scanned = {"v": False}

        def show_tick():
            """扫码成功：蒙版 + 绿圆白勾（image 覆盖，永远最上层）+ 提示切换"""
            canvas.itemconfig(mask, state="normal")
            if tick_item is not None:
                canvas.itemconfig(tick_item, state="normal")
            tip.config(text="扫码成功！请确认~", fg="#00A651",
                       font=("Microsoft YaHei UI", 10, "bold"))
            scanned["v"] = True
            # 强制 tkinter 立即重绘，避免蒙版/勾延迟到下一个 after 循环才刷新
            root.update_idletasks()

        def worker():
            def on_status(st):
                evt_q.put(("status", st))
            try:
                tok = self._poll_and_exchange(key, sig, state, login_url, hdr,
                                              max_wait, cancel=cancel,
                                              on_status=on_status)
                evt_q.put(("done", tok, None))
                if tok:
                    time.sleep(0.9)   # 让用户看到"蒙版+绿√"后再自动关窗
                evt_q.put(("close",))
            except Exception as e:
                evt_q.put(("done", None, str(e)))
                evt_q.put(("close",))

        def tick():
            try:
                while True:
                    kind = evt_q.get_nowait()
                    if kind[0] == "status":
                        st = kind[1]
                        # 非 NEVER / 非 ERR（含 ING=已扫码待确认、SUCC=已确认）
                        # 都显示蒙版+绿√，避免 ING 极短被跳过时漏勾
                        if st and "NEVER" not in st and "ERR" not in st:
                            show_tick()
                    elif kind[0] == "done":
                        result["token"], result["error"] = kind[1], kind[2]
                    elif kind[0] == "close":
                        root.destroy()
                        return
            except _queue.Empty:
                pass
            if not scanned["v"]:
                left = int(deadline - time.time())
                if left > 0:
                    tip.config(text=f"请使用企微扫码【剩余 {left}s】……", fg="#333333",
                               font=("Microsoft YaHei UI", 10))
                else:
                    tip.config(text="二维码已过期，请重新执行 login", fg="#C0392B")
            root.after(200, tick)

        def on_close():
            cancel["v"] = True
            root.destroy()

        root.protocol("WM_DELETE_WINDOW", on_close)
        tick()
        _th.Thread(target=worker, daemon=True).start()
        root.mainloop()

        if result["error"]:
            raise RuntimeError(result["error"])
        if cancel["v"] and not result["token"]:
            raise RuntimeError("用户取消登录")
        return result["token"]

    def whoami(self):
        """返回当前用户登录信息 / 请求凭证"""
        return {
            "ok": True, "logged_in": bool(self.token),
            "token": self.token,
            "uuid": self.uuid,
            "watermark": self.watermark,
            "login_at": self.login_at,
            "expires_at": self.expires_at,
            "cookies": {
                "hlsdk_token_cp7nb9": self.token or "",
                "hl_siteid_8980": self.token or "",
                "hl_uuid": self.uuid or "",
            },
            "endpoints": {
                "get": BASE_GET, "api": BASE_API, "site": SITE,
            },
        }

    def search(self, keyword, page=1, pagesize=10):
        """关键字搜索（分页）"""
        r = self._get(f"{BASE_GET}/foreground/tannant/search-tannant", {
            "tannant_id": TENANT_ID, "keyword": keyword, "page": page,
            "pagesize": pagesize, "uuid": self.uuid, "search_item": "all",
            "content_ids": "", "tag_names": ""})
        d = r.json().get("data") or {}
        lst = d.get("list") or []
        total = d.get("total") or 0
        items = [{
            "name": it.get("name", ""),
            "slug": it.get("slug", ""),
            "type": it.get("type", ""),          # 1=目录 2=文档
            "summary": _plain(it.get("summary", "") or it.get("description", "")),
        } for it in lst]
        return {"ok": True, "keyword": keyword, "total": total,
                "page": int(d.get("page") or page),
                "page_size": int(d.get("page_size") or pagesize),
                "total_pages": (total + pagesize - 1) // pagesize if total else 0,
                "list": items}

    def _search_page(self, args):
        kw, page, ps = args
        r = self._get(f"{BASE_GET}/foreground/tannant/search-tannant", {
            "tannant_id": TENANT_ID, "keyword": kw, "page": page, "pagesize": ps,
            "uuid": self.uuid, "search_item": "all", "content_ids": "", "tag_names": ""})
        d = r.json().get("data") or {}
        return d.get("list") or [], d.get("total") or 0

    def search_all_pages(self, keyword, pagesize=10):
        """并发搜索全部命中页（阶段2并发能力）"""
        first, total = self._search_page((keyword, 1, pagesize))
        items = list(first)
        pages = (total + pagesize - 1) // pagesize
        if pages > 1:
            with ThreadPoolExecutor(max_workers=self.workers) as ex:
                futs = [ex.submit(self._search_page, (keyword, p, pagesize))
                        for p in range(2, pages + 1)]
                for f in as_completed(futs):
                    lst, _ = f.result()
                    if lst:
                        items.extend(lst)
        return total, items

    def categories(self):
        """获取栏目/搜索目录（get-list data_type=1）"""
        r = self._get(f"{BASE_GET}/foreground/content/get-list",
                      {"tannant_id": TENANT_ID, "data_type": 1})
        lst = (r.json().get("data") or {}).get("list") or []
        return {"ok": True, "categories": [{
            "name": c.get("name", ""), "slug": c.get("slug", ""),
            "type": c.get("type", ""), "id": c.get("id", ""),
        } for c in lst]}

    def children(self, slug, recursive=False, _path="", _depth=0, _seen=None):
        """获取目录子项（get-content 的 child 字段，实测确认）；recursive 递归展开（去重防环）"""
        seen = _seen if _seen is not None else {slug}
        d = self._detail_raw(slug)
        childs = d.get("child") or []
        out = []
        for c in childs:
            node = {"name": c.get("name", ""), "slug": c.get("slug", ""),
                    "type": c.get("type", ""),
                    "path": (_path + "/" + c.get("name", "")) if _path else c.get("name", "")}
            out.append(node)
            cs = c.get("slug")
            if recursive and c.get("type") == "1" and cs and cs not in seen and _depth < 8:
                seen.add(cs)
                try:
                    out.extend(self.children(cs, True, node["path"], _depth + 1, seen)["items"])
                except Exception:
                    pass
        return {"ok": True, "slug": slug, "count": len(out),
                "recursive": recursive, "items": out}

    def summary(self, slug):
        """获取搜索结果摘要/介绍：标题 + 正文前 200 字 + 附件/子项概览"""
        d = self._detail_raw(slug)
        text = _plain((d.get("content") or {}).get("content", ""))
        res = _extract_resources((d.get("content") or {}).get("content", ""))
        childs = d.get("child") or []
        return {"ok": True,
                "name": d.get("name", ""), "slug": slug, "type": d.get("type", ""),
                "summary": text[:200] + ("..." if len(text) > 200 else ""),
                "text_length": len(text),
                "attachments": len(res),
                "children": len(childs),
                "updated_at": d.get("update_time", ""),
                "next_step": {"hint": "查看完整内容: python leyou_cloud.py detail " + slug}}

    def detail(self, slug, with_html=False):
        """获取完整页面内容：标题/正文/附件列表/目录子项"""
        d = self._detail_raw(slug)
        html = (d.get("content") or {}).get("content", "") or ""
        res = _extract_resources(html)
        childs = d.get("child") or []
        out = {"ok": True, "name": d.get("name", ""), "slug": slug,
               "type": d.get("type", ""),
               "text": _plain(html),
               "attachments": res,
               "children": [{"name": c.get("name", ""), "slug": c.get("slug", ""),
                             "type": c.get("type", "")} for c in childs],
               "updated_at": d.get("update_time", "")}
        if with_html:
            out["html"] = html
        return out

    def _detail_raw(self, slug):
        r = self._get(f"{BASE_GET}/foreground/content/get-content",
                      {"tannant_id": TENANT_ID, "slug": slug})
        return r.json().get("data") or {}

    # ---------- 翻页（下一页 / 上一页 / 跳页） ----------
    def next_page(self, keyword, page, pagesize=10):
        return self.search(keyword, page + 1, pagesize)

    def prev_page(self, keyword, page, pagesize=10):
        return self.search(keyword, max(1, page - 1), pagesize)

    def jump(self, keyword, page, pagesize=10):
        return self.search(keyword, max(1, int(page)), pagesize)

    # ---------- 下载 ----------
    def download(self, url, out=None, out_dir=None):
        """下载单个附件（PDF/PPT/图片等）；resource-wangsu 免鉴权带 Referer 最稳"""
        name = os.path.basename(url.split("?")[0].split("#")[0]) or "download.bin"
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
            out = os.path.join(out_dir, name)
        elif not out:
            out = name
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with self.session.get(url, headers={"Referer": SITE + "/"},
                              stream=True, timeout=60) as r:
            r.raise_for_status()
            size = 0
            with open(out, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
                    size += len(chunk)
        return {"ok": True, "url": url, "file": os.path.abspath(out),
                "size": size, "kind": _kind_of(url)}

    def get_attachments(self, slug, out_dir="downloads"):
        """并发下载某文档的全部附件（PDF/PPT/图片等）"""
        d = self._detail_raw(slug)
        html = (d.get("content") or {}).get("content", "") or ""
        res = _extract_resources(html)
        if not res:
            return {"ok": True, "slug": slug, "downloaded": [], "failed": [],
                    "message": "该文档没有附件"}
        dir_slug = re.sub(r"[^\w\-]", "_", slug)
        target = os.path.join(out_dir, dir_slug)
        os.makedirs(target, exist_ok=True)
        results, failed = [], []
        def _dl(item):
            try:
                r = self.download(item["url"], out_dir=target)
                return r, item
            except Exception as e:
                return {"ok": False, "url": item["url"], "error": str(e)}, item
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = [ex.submit(_dl, it) for it in res]
            for f in as_completed(futs):
                r, it = f.result()
                (results if r.get("ok") else failed).append({**r, "name": it["name"]})
        return {"ok": True, "slug": slug, "attachments": len(res),
                "downloaded": results, "failed": failed,
                "dir": os.path.abspath(target)}

    def collect(self, keyword, out="", download_dir=None, pagesize=10):
        """并发全库采集：并发搜全部页 → 并发抓全部详情 →（可选）并发下载附件"""
        total, items = self.search_all_pages(keyword, pagesize)
        # 去重 slug
        seen, slugs = set(), []
        for it in items:
            s = it.get("slug")
            if s and s not in seen:
                seen.add(s)
                slugs.append(s)

        docs, failed = [], []
        def _detail(sl):
            try:
                d = self._detail_raw(sl)
                if not d.get("name"):
                    return None, sl
                html = (d.get("content") or {}).get("content", "") or ""
                return {"name": d["name"], "slug": sl, "type": d.get("type", ""),
                        "text": _plain(html),
                        "attachments": _extract_resources(html)}, None
            except Exception:
                return None, sl
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = [ex.submit(_detail, s) for s in slugs]
            for f in as_completed(futs):
                doc, sl = f.result()
                (docs if doc else failed).append(doc or {"slug": sl})

        saved = ""
        if out:
            payload = {"keyword": keyword, "total_hits": total,
                       "fetched": len(docs), "failed": len(failed),
                       "docs": docs}
            with open(out, "w", encoding="utf-8") as fp:
                json.dump(payload, fp, ensure_ascii=False, indent=2)
            saved = os.path.abspath(out)

        att_dl = None
        if download_dir:
            att_dl = []
            def _dl_att(doc):
                try:
                    return self.get_attachments(doc["slug"], out_dir=download_dir)
                except Exception:
                    return None
            with ThreadPoolExecutor(max_workers=self.workers) as ex:
                for f in as_completed([ex.submit(_dl_att, d) for d in docs]):
                    r = f.result()
                    if r and r.get("ok"):
                        att_dl.append({"slug": r["slug"], "downloaded": len(r.get("downloaded", []))})

        return {"ok": True, "keyword": keyword, "total_hits": total,
                "searched_items": len(items), "fetched": len(docs),
                "failed": [f for f in failed if f],
                "saved": saved, "download_dir": os.path.abspath(download_dir) if download_dir else "",
                "attachments_downloaded": att_dl}

    # ---------- AI 问答（SSE） ----------
    def ask(self, question, stream_print=True):
        r = self.session.get(f"{BASE_API}/foreground/chats/stream",
                             params={"tannant_id": TENANT_ID, "token": self.token,
                                     "question": question, "content_ids": ""},
                             stream=True, timeout=120)
        full = []
        for line in r.iter_lines(decode_unicode=True):
            if line:
                full.append(line)
                if stream_print:
                    print(line, flush=True)
        return {"ok": True, "question": question, "raw_lines": full}


# ---------------- CLI ----------------
def _build_parser():
    p = argparse.ArgumentParser(
        prog="leyou_cloud",
        description="乐药云智库 API 完整封装：阶段1登录凭证 + 阶段2搜索/详情/翻页/下载，全功能并发",
        epilog="示例: python leyou_cloud.py search 毛利 --page 1 | python leyou_cloud.py collect 毛利 --workers 12 --download")
    p.add_argument("--token", help="手动指定 token（默认读 token 文件）")
    p.add_argument("--token-file", default=DEFAULT_TOKEN_FILE, help="token 持久化文件路径")
    p.add_argument("--workers", type=int, default=argparse.SUPPRESS, help="并发数 1-50（默认10）")
    p.add_argument("--no-auto-login", action="store_true", default=False, help="token 失效时不自动扫码，直接报错退出码3")
    p.add_argument("--compact", action="store_true", default=argparse.SUPPRESS, help="单行 JSON 输出（机器解析友好）")
    p.add_argument("--qr-out", default=argparse.SUPPRESS, help="二维码图片保存路径（弹窗不可用/--no-popup 时使用）")
    p.add_argument("--wait", type=int, default=argparse.SUPPRESS, help="扫码等待秒数（默认300）")
    p.add_argument("--no-popup", action="store_true", default=argparse.SUPPRESS, help="不弹出二维码窗口，改用图片文件模式")

    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, help_, *pos_args):
        sp = sub.add_parser(name, help=help_)
        # 全局参数下沉到子命令，支持 `cmd --compact` 写法。
        # default 用 SUPPRESS：未显式提供时不写 namespace，
        # 避免子解析器(独立 namespace)的 default 覆盖命令前传入的值
        sp.add_argument("--compact", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        sp.add_argument("--workers", type=int, default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        sp.add_argument("--qr-out", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        sp.add_argument("--wait", type=int, default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        sp.add_argument("--no-popup", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        for a in pos_args:
            sp.add_argument(*a[0], **a[1])
        return sp

    add("status", "检查登录状态（阶段1）")
    add("login", "扫码登录并保存凭证（阶段1）")
    add("search", "关键字搜索", (("keyword",), {"help": "搜索关键词"}),
        (("--page",), {"type": int, "default": 1}),
        (("--pagesize",), {"type": int, "default": 10}))
    add("categories", "获取栏目/搜索目录")
    add("children", "获取目录子项（可递归）", (("slug",), {"help": "目录 slug"}),
        (("--recursive",), {"action": "store_true", "help": "递归展开全部子项"}))
    add("summary", "获取摘要/介绍", (("slug",), {"help": "文档 slug"}))
    add("detail", "获取完整页面内容", (("slug",), {"help": "文档 slug"}),
        (("--html",), {"action": "store_true", "help": "同时输出正文 HTML"}))
    add("next", "下一页", (("keyword",), {}), (("page",), {"type": int, "help": "当前页"}),
        (("--pagesize",), {"type": int, "default": 10}))
    add("prev", "上一页", (("keyword",), {}), (("page",), {"type": int, "help": "当前页"}),
        (("--pagesize",), {"type": int, "default": 10}))
    add("jump", "跳转指定页", (("keyword",), {}), (("page",), {"type": int, "help": "目标页"}),
        (("--pagesize",), {"type": int, "default": 10}))
    add("download", "下载单个附件", (("url",), {"help": "附件直链"}),
        (("--out",), {"default": ""}), (("--out-dir",), {"default": ""}))
    add("get-attachments", "并发下载文档全部附件", (("slug",), {}),
        (("--dir",), {"default": "downloads"}))
    add("collect", "并发全库采集", (("keyword",), {}),
        (("--out",), {"default": "", "help": "保存 JSON 路径"}),
        (("--download",), {"default": "", "help": "附件下载目录"}),
        (("--pagesize",), {"type": int, "default": 10}))
    add("ask", "AI 助手问答（SSE 流式）", (("question",), {}))
    return p


def main(argv=None):
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    args = _build_parser().parse_args(argv)
    # 全局参数兜底归一（default=SUPPRESS 时未显式提供则无属性）
    args.compact = getattr(args, "compact", False)
    args.no_popup = getattr(args, "no_popup", False)
    args.qr_out = getattr(args, "qr_out", "qrcode.png")
    args.wait = getattr(args, "wait", 300)
    args.workers = getattr(args, "workers", 10)
    cloud = LeyouCloud(token=args.token, token_file=args.token_file,
                       workers=args.workers, auto_login=not args.no_auto_login)

    def need_login():
        """阶段1入口：有效直接过；无效自动登录（默认弹窗）；禁止自动登录则抛 LoginRequired"""
        ok, _ = cloud.check_login()
        if not ok and cloud.auto_login:
            cloud.login(qr_out=args.qr_out, max_wait=args.wait, quiet=True,
                        popup=not args.no_popup)
        elif not ok:
            raise LoginRequired
        return cloud.whoami()

    def run2(fn, *a, **kw):
        """阶段2统一执行入口：执行中 token 失效（507）→ 自动回阶段1重登 → 重试原命令一次。
        兑现「失效自动回退重试」承诺；--no-auto-login 时直接抛 TokenExpired（退出码3）。"""
        try:
            return fn(*a, **kw)
        except TokenExpired:
            if not cloud.auto_login:
                raise
            cloud.login(qr_out=args.qr_out, max_wait=args.wait, quiet=True,
                        popup=not args.no_popup)
            return fn(*a, **kw)

    try:
        cmd = args.cmd
        if cmd == "status":
            ok, info = cloud.check_login()
            out = {"ok": True, "logged_in": ok, **info, **cloud.whoami()}
            if not ok:
                out["hint"] = "执行: python leyou_cloud.py login 扫码登录"
        elif cmd == "login":
            out = cloud.login(qr_out=args.qr_out, max_wait=args.wait,
                              popup=not args.no_popup)
        elif cmd == "search":
            need_login(); out = run2(cloud.search, args.keyword, args.page, args.pagesize)
        elif cmd == "categories":
            need_login(); out = run2(cloud.categories)
        elif cmd == "children":
            need_login(); out = run2(cloud.children, args.slug, args.recursive)
        elif cmd == "summary":
            need_login(); out = run2(cloud.summary, args.slug)
        elif cmd == "detail":
            need_login(); out = run2(cloud.detail, args.slug, args.html)
        elif cmd == "next":
            need_login(); out = run2(cloud.next_page, args.keyword, args.page, args.pagesize)
        elif cmd == "prev":
            need_login(); out = run2(cloud.prev_page, args.keyword, args.page, args.pagesize)
        elif cmd == "jump":
            need_login(); out = run2(cloud.jump, args.keyword, args.page, args.pagesize)
        elif cmd == "download":
            need_login(); out = run2(cloud.download, args.url, args.out, args.out_dir)
        elif cmd == "get-attachments":
            need_login(); out = run2(cloud.get_attachments, args.slug, args.dir)
        elif cmd == "collect":
            need_login(); out = run2(cloud.collect, args.keyword, args.out, args.download, args.pagesize)
        elif cmd == "ask":
            need_login(); out = run2(cloud.ask, args.question, not args.compact)
        else:
            out = {"ok": False, "error": "UNKNOWN_COMMAND", "message": "未知命令: " + cmd}
            _emit(out, args.compact)
            return 2

        _emit(out, args.compact)
        return 0

    except TokenExpired:
        _emit({"ok": False, "error": "TOKEN_EXPIRED",
               "message": "token 已失效",
               "hint": "执行: python leyou_cloud.py login 扫码获取新凭证（或 --no-auto-login 时手动处理）"}, args.compact)
        return 3
    except LoginRequired as e:
        _emit({"ok": False, "error": "LOGIN_REQUIRED",
               "message": str(e) or "需要登录",
               "hint": "执行: python leyou_cloud.py login"}, args.compact)
        return 3
    except requests.exceptions.RequestException as e:
        _emit({"ok": False, "error": "NETWORK_ERROR", "message": repr(e)[:200]}, args.compact)
        return 4
    except (RuntimeError, ValueError, KeyError) as e:
        _emit({"ok": False, "error": "BUSINESS_ERROR", "message": str(e)[:300]}, args.compact)
        return 1


if __name__ == "__main__":
    sys.exit(main())

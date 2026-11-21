"""Gọi trade API của pathofexile.com (realm Sony), có giới hạn tốc độ và nhớ tạm ngắn hạn."""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict, deque

import requests

BASE = "https://www.pathofexile.com"
USER_AGENT = "poe-tv-pricecheck/0.1 (personal console price check)"
CACHE_TTL = 600          # giây; chỉ nhớ trong 10 phút để khỏi tra lại cùng một món
MAX_WAIT = 15            # nếu phải chờ lâu hơn số giây này thì báo bận thay vì treo


class RateLimited(Exception):
    def __init__(self, wait):
        super().__init__(f"Đang bị giới hạn tốc độ, thử lại sau {int(wait)} giây")
        self.wait = wait


class Trade:
    def __init__(self, realm="sony", league=None, session=None):
        self.realm = realm
        # Gọi API với User-Agent ghi rõ tên công cụ, không giả dạng trình duyệt
        self.s = session or requests.Session()
        self.s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        self.league = league
        self.rules = {}                      # policy -> [(max, window, timeout)]
        self.sent = defaultdict(deque)       # policy -> thời điểm các lần gọi
        self.blocked_until = defaultdict(float)
        self.cache = {}

    # ---------------- giới hạn tốc độ ----------------
    def _wait_needed(self, policy):
        now = time.time()
        if self.blocked_until[policy] > now:
            return self.blocked_until[policy] - now
        need = 0.0
        q = self.sent[policy]
        for mx, window, _ in self.rules.get(policy, []):
            recent = [t for t in q if now - t < window]
            if len(recent) >= mx - 1:          # chừa 1 lần để an toàn
                need = max(need, window - (now - recent[0]) + 0.2)
        return need

    def _learn(self, policy, resp):
        rules = []
        for key in ("X-Rate-Limit-Ip", "X-Rate-Limit-Account"):
            v = resp.headers.get(key)
            if v:
                for part in v.split(","):
                    mx, window, timeout = (int(x) for x in part.split(":"))
                    rules.append((mx, window, timeout))
        if rules:
            self.rules[policy] = rules
        if resp.status_code == 429:
            ra = float(resp.headers.get("Retry-After", 60))
            self.blocked_until[policy] = time.time() + ra
            raise RateLimited(ra)

    def _call(self, policy, method, url, **kw):
        w = self._wait_needed(policy)
        if w > MAX_WAIT:
            raise RateLimited(w)
        if w > 0:
            time.sleep(w)
        self.sent[policy].append(time.time())
        while len(self.sent[policy]) > 200:
            self.sent[policy].popleft()
        r = self.s.request(method, url, timeout=15, **kw)
        self._learn(policy, r)
        if r.status_code == 403:
            raise RuntimeError("Trang trade từ chối (403). Không cố vượt qua; dùng link mở trình duyệt bên dưới")
        r.raise_for_status()
        return r.json()

    # ---------------- API ----------------
    def pick_league(self):
        if self.league:
            return self.league
        data = self._call("data", "GET", f"{BASE}/api/trade/data/leagues")
        cands = [l["id"] for l in data["result"] if l.get("realm") == self.realm]
        for lid in cands:
            if not any(w in lid for w in ("Hardcore", "HC ", "Ruthless", "Standard")):
                self.league = lid
                return lid
        self.league = cands[0]
        return self.league

    def search(self, query, fetch=10):
        league = self.pick_league()
        key = json.dumps(query, sort_keys=True)
        hit = self.cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return {**hit[1], "cached": True}
        res = self._call("search", "POST", f"{BASE}/api/trade/search/{self.realm}/{league}", json=query)
        listings = []
        ids = res.get("result", [])[:fetch]
        if ids:
            got = self._call("fetch", "GET",
                             f"{BASE}/api/trade/fetch/{','.join(ids)}?query={res['id']}&realm={self.realm}")
            for x in got.get("result", []):
                if not x:
                    continue
                p = (x.get("listing") or {}).get("price") or {}
                listings.append({
                    "amount": p.get("amount"), "currency": p.get("currency"),
                    "name": f"{x['item'].get('name', '')} {x['item'].get('typeLine', '')}".strip(),
                    "ilvl": x["item"].get("ilvl"), "indexed": x["listing"].get("indexed"),
                    "mods": [m if isinstance(m, str) else str(m) for m in x["item"].get("explicitMods", [])],
                })
        out = {"total": res.get("total", 0), "league": league, "listings": listings,
               "url": f"{BASE}/trade/search/{self.realm}/{league}/{res.get('id')}", "cached": False}
        self.cache[key] = (time.time(), out)
        return out


    def exchange(self, tag, status="online"):
        """Tra giá đồ bán theo lô (currency, fragment, offering, scarab…) trên sàn trao đổi: 1 lần gọi là có giá,
        không cần bước fetch. Trả giá mỗi món tính bằng chaos và bằng divine (ai bán rẻ nhất)."""
        league = self.pick_league()
        have = ["divine"] if tag == "chaos" else ["chaos", "divine"]
        body = {"engine": "new", "query": {"status": {"option": status}, "have": have, "want": [tag]},
                "sort": {"have": "asc"}}
        key = json.dumps(body, sort_keys=True)
        hit = self.cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return {**hit[1], "cached": True}
        res = self._call("exchange", "POST", f"{BASE}/api/trade/exchange/{self.realm}/{league}", json=body)
        offers = []
        for r in (res.get("result") or {}).values():
            lst = (r or {}).get("listing") or {}
            if len(lst.get("offers") or []) != 1:
                continue
            o = lst["offers"][0]
            if not o["item"].get("amount"):
                continue
            offers.append({"each": o["exchange"]["amount"] / o["item"]["amount"], "currency": o["exchange"]["currency"],
                           "stock": o["item"].get("stock")})
        best = {}
        for o in offers:
            if o["currency"] not in best or o["each"] < best[o["currency"]]["each"]:
                best[o["currency"]] = o
        out = {"total": res.get("total", 0), "league": league, "best": best, "sellers": len(offers),
               "url": f"{BASE}/trade/exchange/{self.realm}/{league}/{res.get('id')}", "cached": False}
        self.cache[key] = (time.time(), out)
        return out


DEFAULT_LEAGUE = "Covens"   # league 3.30; đổi khi sang league mới nếu không tự lấy được


def browser_url(query, realm="sony", league=None):
    """Link mở thẳng trang trade với bộ lọc đã điền sẵn (dùng khi API lỗi hoặc muốn xem kỹ)."""
    from urllib.parse import quote
    q = json.dumps({"query": query["query"], "sort": query.get("sort", {})}, separators=(",", ":"))
    return f"{BASE}/trade/search/{realm}/{quote(league or DEFAULT_LEAGUE)}?q={quote(q)}"


def summarize(result):
    """Tóm tắt giá: số món, giá rẻ nhất, mức giá phổ biến trong nhóm rẻ nhất."""
    ls = [l for l in result["listings"] if l["amount"] is not None]
    if not ls:
        return {"total": result["total"], "text": "Không tìm thấy món nào đang bán"}
    cheapest = ls[0]
    common = Counter(f"{l['amount']:g} {l['currency']}" for l in ls).most_common(1)[0]
    text = (f"{result['total']} món đang bán | rẻ nhất {cheapest['amount']:g} {cheapest['currency']}"
            f" | phổ biến {common[0]} ({common[1]}/{len(ls)} món rẻ nhất)")
    return {"total": result["total"], "cheapest": f"{cheapest['amount']:g} {cheapest['currency']}",
            "common": common[0], "text": text}

"""Gọi trade API của pathofexile.com, có giới hạn tốc độ và nhớ tạm ngắn hạn.
PoE1: /api/trade/…/{realm}/{league} (realm Sony). PoE2: /api/trade2/…/{league} (như Exiled Exchange 2; PoE2 không chia realm)."""
from __future__ import annotations

import json
import threading
import time
from collections import Counter, defaultdict, deque
from urllib.parse import quote

import requests

BASE = "https://www.pathofexile.com"
USER_AGENT = "poe-tv-pricecheck/0.1 (personal console price check)"
CACHE_TTL = 600          # giây; chỉ nhớ trong 10 phút để khỏi tra lại cùng một món
MAX_WAIT = 15            # nếu phải chờ lâu hơn số giây này thì báo bận thay vì treo


class RateLimited(Exception):
    def __init__(self, wait):
        super().__init__(f"Đang bị giới hạn tốc độ, thử lại sau {int(wait)} giây")
        self.wait = wait


# tiền dùng để định giá đồ bán theo lô: (mã trên sàn, chữ viết tắt hiện trên TV)
MAIN_CURRENCY = {"poe1": (("chaos", "c"), ("divine", "div")), "poe2": (("exalted", "ex"), ("divine", "div"))}


class Trade:
    def __init__(self, realm="sony", league=None, session=None, game="poe1"):
        self.game = game
        self.realm = realm if game == "poe1" else "poe2"
        self.api = "trade" if game == "poe1" else "trade2"
        # Gọi API với User-Agent ghi rõ tên công cụ, không giả dạng trình duyệt
        self.s = session or requests.Session()
        self.s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        self.league = league
        self.rules = {}                      # policy -> [(max, window, timeout)]
        self.sent = defaultdict(deque)       # policy -> thời điểm các lần gọi
        self.blocked_until = defaultdict(float)
        self.cache = {}
        # tra tự động (luồng đọc khung hình) và TRA LẠI trên trang (luồng máy chủ) gọi lần lượt, đúng giới hạn tốc độ
        self._lock = threading.RLock()

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
        with self._lock:
            return self._call_locked(policy, method, url, **kw)

    def _call_locked(self, policy, method, url, **kw):
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
        data = self._call("data", "GET", f"{BASE}/api/{self.api}/data/leagues")
        cands = [l["id"] for l in data["result"] if l.get("realm", self.realm) == self.realm]
        for lid in cands:
            if not any(w in lid for w in ("Hardcore", "HC ", "HC)", "Ruthless", "Standard", "SSF", "Solo")):
                self.league = lid
                return lid
        self.league = cands[0]
        return self.league

    def _path(self, kind, league):
        """Đường dẫn API search/exchange: PoE1 có realm, PoE2 thì không."""
        if self.game == "poe1":
            return f"{BASE}/api/trade/{kind}/{self.realm}/{quote(league)}"
        return f"{BASE}/api/trade2/{kind}/{quote(league)}"

    def _site(self, kind, league, qid):
        """Link mở trang trade trên trình duyệt."""
        return f"{BASE}/{self.api}/{kind}/{self.realm}/{quote(league)}/{qid}"

    def search(self, query, fetch=10):
        league = self.pick_league()
        key = json.dumps(query, sort_keys=True)
        hit = self.cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return {**hit[1], "cached": True}
        res = self._call("search", "POST", self._path("search", league), json=query)
        listings = []
        ids = res.get("result", [])[:fetch]
        if ids:
            extra = f"&realm={self.realm}" if self.game == "poe1" else ""
            got = self._call("fetch", "GET", f"{BASE}/api/{self.api}/fetch/{','.join(ids)}?query={res['id']}{extra}")
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
               "url": self._site("search", league, res.get("id")), "cached": False}
        self.cache[key] = (time.time(), out)
        return out


    def exchange_any(self, tag):
        """Như exchange; không ai online bán thì tính cả người offline."""
        res = self.exchange(tag, "online")
        if not res["best"]:
            res = {**self.exchange(tag, "any"), "offline": True}
        return res

    # ---- Currency Exchange trong game (Faustus): giao dịch đã khớp theo giờ, API công khai của GGG ----
    CX_URL = "https://web.poecdn.com/api/currency-exchange/{realm}/{hour}"
    CHAOS_ID, DIVINE_ID = "CurrencyRerollRare", "CurrencyModValues"

    def _cx_hour(self, hour):
        key = ("cx", hour)
        if key in self.cache:
            return self.cache[key][1]
        try:
            r = self.s.get(self.CX_URL.format(realm=self.realm, hour=hour), timeout=15)
            data = r.json().get("markets", []) if r.status_code == 200 else None
        except (requests.RequestException, ValueError):
            data = None
        if data is not None:
            self.cache[key] = (time.time(), data)
        return data

    def market(self, stem, hours=3):
        """Giá đã khớp trên Currency Exchange của món có mã nội bộ kết thúc bằng `stem`, gộp `hours` giờ gần nhất.
        Trả {"chaos": giá c/món, "divine": giá div/món, "volume": số món đã khớp} hoặc None nếu không có giao dịch.
        Chỉ PoE1: chưa kiểm chứng API này cho PoE2."""
        if self.game != "poe1":
            return None
        league = self.pick_league()
        now_h = int(time.time()) // 3600 * 3600
        agg = {"chaos": [0.0, 0.0], "divine": [0.0, 0.0]}   # [tổng tiền, tổng món]
        got = 0
        for k in range(1, hours + 2):
            if got >= hours:
                break
            markets = self._cx_hour(now_h - 3600 * k)
            if markets is None:
                continue
            got += 1
            for m in markets:
                if m.get("league") != league:
                    continue
                a, _, b = (m.get("market_id") or "").partition("|")
                vol = m.get("volume_traded") or {}
                for mine, other in ((a, b), (b, a)):
                    if not mine.endswith("/" + stem):
                        continue
                    for cur, cid in (("chaos", self.CHAOS_ID), ("divine", self.DIVINE_ID)):
                        if other.endswith("/" + cid) and vol.get(mine) and vol.get(other):
                            agg[cur][0] += vol[other]
                            agg[cur][1] += vol[mine]
        out = {cur: tot / n for cur, (tot, n) in agg.items() if n}
        if not out:
            return None
        out["volume"] = int(max(n for _, n in agg.values()))
        out["hours"] = got
        return out

    def exchange(self, tag, status="online"):
        """Tra giá đồ bán theo lô (currency, fragment, offering, scarab…) trên sàn trao đổi: 1 lần gọi là có giá,
        không cần bước fetch. Trả giá mỗi món tính bằng tiền chính của game (PoE1 chaos/divine, PoE2 exalted/divine),
        lấy người bán rẻ nhất."""
        league = self.pick_league()
        have = [c for c, _ in MAIN_CURRENCY[self.game] if c != tag]      # không đổi divine lấy chính divine
        body = {"engine": "new", "query": {"status": {"option": status}, "have": have, "want": [tag]},
                "sort": {"have": "asc"}}
        key = json.dumps(body, sort_keys=True)
        hit = self.cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return {**hit[1], "cached": True}
        res = self._call("exchange", "POST", self._path("exchange", league), json=body)
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
               "url": self._site("exchange", league, res.get("id")), "cached": False}
        self.cache[key] = (time.time(), out)
        return out


RATE_TTL = 1800          # tỉ giá nhớ 30 phút


def _rates_from(trade, small, big):
    r = {small: 1.0}
    b = (trade.exchange_any(big).get("best") or {}).get(small)
    if not b or not b.get("each"):
        return None
    r[big] = float(b["each"])
    return r


def get_rates(trade):
    """Giá các loại tiền tính bằng tiền nhỏ của game (PoE1: chaos, PoE2: exalted), lấy trên sàn trao đổi của league,
    nhớ 30 phút. Không lấy được thì trả None (ô giá quay về cách hiện cũ)."""
    hit = getattr(trade, "_rates", None)
    if hit and time.time() - hit[0] < RATE_TTL:
        return hit[1]
    (small, _), (big, _) = MAIN_CURRENCY[trade.game]
    r = _rates_from(trade, small, big)
    if r and trade.game == "poe2":
        try:                                   # PoE2 vẫn có người rao giá bằng chaos
            c = (trade.exchange_any("chaos").get("best") or {}).get(small)
            if c and c.get("each"):
                r["chaos"] = float(c["each"])
        except Exception:
            pass
    trade._rates = (time.time(), r)
    return r


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

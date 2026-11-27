"""Облачный бот ТГ-канала (GitHub Actions, раз в 5–15 мин). Без внешних библиотек.
Черновик живёт в личке Паши с ботом (его шлёт tg_review.py send). Кнопки несут id сообщений поста:
  pub:<mid>:<n>  опубликовать сейчас (copyMessage/copyMessages из лички в канал)
  sch:<mid>:<n>  выбрать время;  at:<mid>:<n>:<unix>  поставить в очередь;  unq:<mid>:<n>  снять с очереди
  rej:<mid>:<n>  отклонить;  back:<mid>:<n>  назад к кнопкам
Состояние (offset, очередь) в state.json, его коммитит workflow. Содержимого постов в репо нет.
"""
import os, json, time, urllib.request, datetime as dt

TOK, ME = os.environ["TG_TOKEN"], os.environ["TG_CHAT"]
CH = os.environ.get("TG_CHANNEL") or "@coal_not_black"
API = f"https://api.telegram.org/bot{TOK}/"
MSK = dt.timezone(dt.timedelta(hours=3))
SF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
st = json.load(open(SF, encoding="utf-8")) if os.path.exists(SF) else {"offset": 0, "queue": []}

def call(method, **data):
    req = urllib.request.Request(API + method, json.dumps(data).encode(), {"Content-Type": "application/json"})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=60))
    except urllib.error.HTTPError as e:
        r = json.load(e)
    if not r.get("ok"):
        print("WARN", method, r.get("description"))
    return r.get("result")

def kb(rows): return {"inline_keyboard": [[{"text": t, "callback_data": c} for t, c in row] for row in rows]}
def main_kb(mid, n):
    a = f"{mid}:{n}"
    return kb([[("✅ Опубликовать", f"pub:{a}"), ("🕗 Запланировать", f"sch:{a}")], [("❌ Отклонить", f"rej:{a}")]])
def label(ts): return dt.datetime.fromtimestamp(ts, MSK).strftime("%d.%m %H:%M МСК")

def time_kb(mid, n):
    now = dt.datetime.now(MSK); a = f"{mid}:{n}"; opts = []
    for day, name in ((0, "Сегодня"), (1, "Завтра")):
        for h in (10, 14, 19, 22):
            t = (now + dt.timedelta(days=day)).replace(hour=h, minute=0, second=0, microsecond=0)
            if t > now + dt.timedelta(minutes=20):
                opts.append((f"{name} {h}:00", f"at:{a}:{int(t.timestamp())}"))
    opts.append(("Через 1 час", f"at:{a}:{int((now + dt.timedelta(hours=1)).timestamp())}"))
    rows = [opts[i:i + 2] for i in range(0, len(opts), 2)] + [[("← Назад", f"back:{a}")]]
    return kb(rows)

def publish(mid, n):
    if n == 1:
        r = call("copyMessage", chat_id=CH, from_chat_id=ME, message_id=mid)
        ids = [r["message_id"]] if r else []
    else:
        r = call("copyMessages", chat_id=CH, from_chat_id=ME, message_ids=list(range(mid, mid + n)))
        ids = [x["message_id"] for x in r] if r else []
    for i in ids:  # на всякий случай убрать кнопки, если скопировались
        call("editMessageReplyMarkup", chat_id=CH, message_id=i)
    return ids

def set_kb(msg, markup): call("editMessageReplyMarkup", chat_id=ME, message_id=msg, reply_markup=markup)
def done(msg, text): set_kb(msg, kb([[(text, "noop")]]))
link = lambda ids: f"t.me/{CH.lstrip('@')}/{ids[0]}" if ids else "?"

# 1) нажатия
ups = call("getUpdates", offset=st["offset"], timeout=0, allowed_updates=["callback_query"]) or []
# пропавшие нажатия: номер первого пришедшего больше ожидаемого — значит, кто-то другой забрал их тем же токеном
if ups and st["offset"] and ups[0]["update_id"] > st["offset"]:
    gap = ups[0]["update_id"] - st["offset"]
    print(f"LOST {gap} нажатий: ждали {st['offset']}, пришло {ups[0]['update_id']}")
    st.setdefault("lost", []).append({"t": label(time.time()), "from": st["offset"], "to": ups[0]["update_id"] - 1})
    st["lost"] = st["lost"][-20:]
for u in ups:
    st["offset"] = u["update_id"] + 1
    q = u.get("callback_query")
    if not q: continue
    ans = lambda t: call("answerCallbackQuery", callback_query_id=q["id"], text=t)
    if str(q["from"]["id"]) != ME: ans("Только для админа"); continue
    p = (q.get("data") or "").split(":")
    if len(p) < 3 or not p[1].isdigit(): ans("Старая кнопка"); continue
    act, mid, n = p[0], int(p[1]), int(p[2]); km = q["message"]["message_id"]
    inq = [x for x in st["queue"] if x["mid"] == mid]
    if act == "pub":
        if inq: st["queue"] = [x for x in st["queue"] if x["mid"] != mid]
        ids = publish(mid, n); done(km, "✅ Опубликовано" if ids else "⚠️ Ошибка публикации"); ans("Готово")
    elif act == "sch": set_kb(km, time_kb(mid, n)); ans("Выбери время")
    elif act == "at":
        ts = int(p[3]); st["queue"] = [x for x in st["queue"] if x["mid"] != mid] + [{"mid": mid, "n": n, "ts": ts, "kb": km}]
        set_kb(km, kb([[(f"🕗 {label(ts)}", "noop")], [("Отменить план", f"unq:{mid}:{n}")]])); ans("Запланировано")
    elif act == "unq":
        st["queue"] = [x for x in st["queue"] if x["mid"] != mid]; set_kb(km, main_kb(mid, n)); ans("План снят")
    elif act == "back": set_kb(km, main_kb(mid, n)); ans("")
    elif act == "rej":
        st["queue"] = [x for x in st["queue"] if x["mid"] != mid]; done(km, "❌ Отклонено"); ans("Отклонено")

# 2) очередь
now = time.time(); keep = []
for x in st["queue"]:
    if x["ts"] <= now:
        ids = publish(x["mid"], x["n"])
        done(x["kb"], "✅ Вышел по плану" if ids else "⚠️ Ошибка публикации")
        call("sendMessage", chat_id=ME, text=f"✅ Запланированный пост вышел: {link(ids)}" if ids else "⚠️ Запланированный пост не вышел, смотри логи")
    else:
        keep.append(x)
st["queue"] = keep
json.dump(st, open(SF, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("updates", len(ups), "queue", len(keep), "first", ups[0]["update_id"] if ups else "-", "offset", st["offset"])

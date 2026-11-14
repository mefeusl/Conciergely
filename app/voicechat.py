"""Voice setup: a new client answers the setup questions by speaking, on a personal link.

The link is /talk/<token>. Only a hash of the token is stored, it expires (VOICE_LINK_HOURS) and each link allows a
limited number of turns (VOICE_MAX_TURNS), which caps what one client can cost in OpenAI usage.
A turn = recording (or typed text) -> Whisper -> the SAME setup logic as the WhatsApp chat (onboarding.handle) -> spoken reply.
Off until VOICE_SETUP=1 and an OpenAI key and PUBLIC_BASE_URL are set.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import secrets
from datetime import timedelta

from . import config, db, i18n, plans, sitepage, tts, voice

MAX_TEXT = 600

STRINGS = {
    "tr": {
        "title": "Sesli kurulum", "hello": "Merhaba! Kurulum sorularını burada sesli yanıtlayabilirsiniz.",
        "start": "Sohbeti başlat", "speaking": "Konuşuyor... cevaplamak için dokunun", "listening": "Dinliyorum... bitirmek için dokunun",
        "resume": "Devam etmek için dokunun", "silence": "Bir şey duymadım.", "busy": "Düşünüyorum...",
        "or": "ya da yazın", "send": "Gönder", "heard": "Duyduğum", "done": "Kurulum tamam. WhatsApp'a dönebilirsiniz.",
        "mic": "Mikrofona izin verilmedi. Aşağıya yazarak devam edebilirsiniz.", "error": "Bir sorun oldu, tekrar deneyin.",
        "limit": "Bu bağlantının sınırı doldu. Kalan soruları WhatsApp'tan yanıtlayabilirsiniz.",
        "gone": "Bu bağlantı geçersiz ya da süresi dolmuş. WhatsApp'tan yeni bir bağlantı isteyin.",
        "offer": "İsterseniz kurulumu sesli yapabilirsiniz: {url}",
    },
    "en": {
        "title": "Voice setup", "hello": "Hi! You can answer the setup questions here by speaking.",
        "start": "Start the conversation", "speaking": "Speaking... tap to answer", "listening": "Listening... tap when done",
        "resume": "Tap to continue", "silence": "I didn't hear anything.", "busy": "Thinking...",
        "or": "or type", "send": "Send", "heard": "I heard", "done": "Setup is done. You can go back to WhatsApp.",
        "mic": "Microphone access was not allowed. You can keep going by typing below.", "error": "Something went wrong, try again.",
        "limit": "This link has reached its limit. You can answer the rest on WhatsApp.",
        "gone": "This link is invalid or has expired. Ask for a new one on WhatsApp.",
        "offer": "If you prefer, you can do the setup by voice: {url}",
    },
}


def s(lang: str) -> dict:
    return STRINGS.get(lang) or STRINGS["en"]


def enabled() -> bool:
    return bool(config.VOICE_SETUP and config.OPENAI_API_KEY and config.PUBLIC_BASE_URL.startswith("https://"))


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_link(phone: str) -> str:
    """Make a fresh personal link for this client. The token is shown once, only its hash is kept."""
    token = secrets.token_urlsafe(24)
    now = db.now_utc()
    with db.conn() as c:
        c.execute("DELETE FROM voice_links WHERE phone=? OR expires_at<?", (phone, db.to_iso(now)))
        c.execute("INSERT INTO voice_links(token_hash, phone, expires_at, turns, created_at) VALUES(?,?,?,?,?)",
                  (_hash(token), phone, db.to_iso(now + timedelta(hours=config.VOICE_LINK_HOURS)), 0, db.to_iso(now)))
    return config.PUBLIC_BASE_URL.rstrip("/") + "/talk/" + token


def offer_text(client: dict) -> str:
    """One sentence with a personal link to add to the greeting, or "" when voice setup is off."""
    if not enabled():
        return ""
    return s(client["lang"])["offer"].format(url=new_link(client["phone"]))


def client_for(token: str) -> dict | None:
    if not token or len(token) > 100:
        return None
    with db.conn() as c:
        row = c.execute("SELECT * FROM voice_links WHERE token_hash=?", (_hash(token),)).fetchone()
    if not row:
        return None
    expires = db.from_iso(row["expires_at"])
    if not expires or expires <= db.now_utc():
        return None
    return db.get_client(row["phone"])


def _use_turn(token: str) -> bool:
    """Count one turn; False when the link is out of turns."""
    with db.conn() as c:
        cur = c.execute("UPDATE voice_links SET turns=turns+1 WHERE token_hash=? AND turns<?",
                        (_hash(token), config.VOICE_MAX_TURNS))
        return cur.rowcount == 1


def prompt_of(client: dict) -> str:
    """What the page should say now: the current setup question, or the finished message."""
    st = s(client["lang"])
    if client.get("onboarding_done"):
        return st["done"]
    step = max(1, int(client.get("onboarding_step") or 1))
    plan = plans.plan_of(client)
    return i18n.question(client["lang"], min(step, i18n.total_for(plan)), plan)


def _audio_of(text: str) -> str | None:
    try:
        raw = tts.speak(text, "mp3")
    except Exception:
        return None  # the page still shows the text
    return base64.b64encode(raw).decode() if raw else None


def turn(token: str, audio: bytes | None, content_type: str, text: str) -> dict:
    """One exchange. Returns {ok, heard, reply, audio, done} or {ok: False, error}."""
    client = client_for(token)
    if not client:
        return {"ok": False, "error": "gone"}
    if client.get("onboarding_done"):
        return {"ok": True, "heard": "", "reply": s(client["lang"])["done"], "audio": None, "done": True}
    if audio is not None:
        if not audio or len(audio) > config.VOICE_MAX_AUDIO:
            return {"ok": False, "error": "error"}
        if not _use_turn(token):
            return {"ok": False, "error": "limit"}
        try:
            heard = voice.transcribe(audio, content_type, client) or ""
        except Exception:
            return {"ok": False, "error": "error"}
    else:
        heard = " ".join((text or "").split())[:MAX_TEXT]
        if heard and not _use_turn(token):
            return {"ok": False, "error": "limit"}
    if not heard:  # opening the conversation: speak the current question (counts as a turn, so it cannot be abused)
        if not _use_turn(token):
            return {"ok": False, "error": "limit"}
        question = prompt_of(client)
        return {"ok": True, "heard": "", "reply": question, "audio": _audio_of(question), "done": False}
    from . import onboarding  # imported here because onboarding imports this module

    try:
        reply = onboarding.handle(client, heard)
    except Exception:
        return {"ok": False, "error": "error"}
    fresh = db.get_client(client["phone"]) or client
    return {"ok": True, "heard": heard, "reply": reply, "audio": _audio_of(reply),
            "done": bool(fresh.get("onboarding_done"))}


# ---------- the page ----------

_PAGE = """<!doctype html>
<html lang="__LANG__"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>__TITLE__ · __BOT__</title>
<link rel="icon" href="/static-site/favicon.svg">
<style>
:root{--bg:#f5f5f7;--card:#fff;--ink:#1d1d1f;--mute:#6e6e73;--blue:#0066cc;--line:#d2d2d7;--red:#c9342a}
@media (prefers-color-scheme:dark){:root{--bg:#000;--card:#1c1c1e;--ink:#f5f5f7;--mute:#a1a1a6;--blue:#2997ff;--line:#38383a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;display:flex;justify-content:center;padding:24px 16px}
main{width:100%;max-width:520px}h1{font-size:28px;margin:8px 0 4px;letter-spacing:-.02em}
.card{background:var(--card);border-radius:18px;padding:20px;margin:16px 0}.q{font-size:20px;line-height:1.4}
.mute{color:var(--mute);font-size:15px}.c{text-align:center}.err{color:var(--red)}
#mic{display:block;margin:20px auto 8px;width:104px;height:104px;border-radius:50%;border:0;background:var(--blue);color:#fff;font-size:15px;cursor:pointer}
#mic.rec{background:var(--red)}#mic:disabled{opacity:.5}
form{display:flex;gap:8px;margin-top:8px}input{flex:1;padding:12px;border-radius:12px;border:1px solid var(--line);background:var(--card);color:var(--ink);font:inherit}
button.send{padding:12px 18px;border-radius:12px;border:0;background:var(--blue);color:#fff;font:inherit;cursor:pointer}
</style></head><body><main>
<h1>__TITLE__</h1><p class="mute" id="hello"></p>
<div class="card"><div class="q" id="q" aria-live="polite"></div><p class="mute" id="heard"></p></div>
<button id="mic" type="button"></button>
<p class="mute" id="msg" aria-live="polite"></p>
<p class="mute c" id="or"></p>
<form id="f"><input id="t" maxlength="600" autocomplete="off"><button class="send" id="s" type="submit"></button></form>
<script type="application/json" id="cfg">__CFG__</script>
<script>
(function(){
var cfg=JSON.parse(document.getElementById('cfg').textContent),$=function(i){return document.getElementById(i)};
var base=location.pathname.split('/').slice(0,3).join('/');
var stream=null,ctx=null,an=null,rec=null,chunks=[],timer=null,sendIt=false,player=null;
var mode='idle',done=false,session=false,started=false;
function setMode(m,label){mode=m;var b=$('mic');b.className=m==='listen'?'rec':'';b.textContent=label;b.disabled=(m==='busy')||done}
function say(m,bad){$('msg').textContent=m||'';$('msg').className=bad?'mute err':'mute'}
function cleanup(){try{if(stream)stream.getTracks().forEach(function(t){t.stop()});if(ctx)ctx.close()}catch(e){}stream=null;ctx=null;an=null}
function ensureStream(){
  if(stream)return Promise.resolve();
  if(!navigator.mediaDevices||!window.MediaRecorder)return Promise.reject(new Error('no mic'));
  return navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true}}).then(function(s){
    stream=s;ctx=new (window.AudioContext||window.webkitAudioContext)();ctx.resume();
    an=ctx.createAnalyser();an.fftSize=1024;ctx.createMediaStreamSource(s).connect(an);
  });
}
function stopRec(send){clearInterval(timer);sendIt=send;if(rec&&rec.state!=='inactive')rec.stop()}
function listen(){
  if(done||mode==='busy'||rec)return;
  ensureStream().then(function(){
    chunks=[];var spoke=false,t0=Date.now(),last=0,data=new Uint8Array(an.fftSize);
    rec=new MediaRecorder(stream);
    rec.ondataavailable=function(e){if(e.data.size)chunks.push(e.data)};
    rec.onstop=function(){
      var send=sendIt,type=(rec&&rec.mimeType)||'audio/webm';rec=null;
      if(send&&chunks.length)post(new Blob(chunks,{type:type}),type.split(';')[0]);
      else{setMode('idle',cfg.resume);say(cfg.silence)}
    };
    rec.start();setMode('listen',cfg.listening);say('');
    timer=setInterval(function(){
      an.getByteTimeDomainData(data);var s=0,i,v;
      for(i=0;i<data.length;i++){v=(data[i]-128)/128;s+=v*v}
      var now=Date.now();
      if(Math.sqrt(s/data.length)>0.03){spoke=true;last=now}
      if(spoke&&now-last>1300)stopRec(true);
      else if(!spoke&&now-t0>9000)stopRec(false);
      else if(now-t0>25000)stopRec(spoke);
    },100);
  }).catch(function(){session=false;setMode('idle',cfg.start);say(cfg.mic,true)});
}
function speak(b64){
  setMode('speak',cfg.speaking);
  try{player=new Audio('data:audio/mpeg;base64,'+b64);
    player.onended=function(){if(session&&!done)listen()};
    player.play().catch(function(){if(session&&!done)listen()});
  }catch(e){if(session&&!done)listen()}
}
function show(r){
  mode='idle';
  if(!r.ok){setMode('idle',session?cfg.resume:cfg.start);say(cfg[r.error]||cfg.error,true);return}
  say('');$('heard').textContent=r.heard?cfg.heard+': '+r.heard:'';
  if(r.reply)$('q').textContent=r.reply;
  if(r.done){done=true;session=false;$('s').disabled=true;setMode('idle','');$('mic').disabled=true;if(r.audio){try{new Audio('data:audio/mpeg;base64,'+r.audio).play().catch(function(){})}catch(e){}}cleanup();return}
  if(r.audio)speak(r.audio);else if(session)listen();else setMode('idle',cfg.start);
}
function post(body,type){
  setMode('busy',cfg.busy);say(cfg.busy);
  fetch(base+'/turn',{method:'POST',headers:{'Content-Type':type},body:body}).then(function(r){return r.json()}).then(show).catch(function(){show({ok:false,error:'error'})});
}
function interrupt(){if(player){player.onended=null;player.pause()}if(rec)stopRec(false)}
$('mic').addEventListener('click',function(){
  if(done||mode==='busy')return;
  if(mode==='listen'){stopRec(true);return}
  if(mode==='speak'){if(player){player.onended=null;player.pause()}listen();return}
  session=true;
  if(!started){started=true;ensureStream().then(function(){post(JSON.stringify({text:''}),'application/json')}).catch(function(){started=false;session=false;say(cfg.mic,true)});return}
  listen();
});
$('f').addEventListener('submit',function(e){
  e.preventDefault();var v=$('t').value.trim();if(!v||done||mode==='busy')return;
  $('t').value='';interrupt();started=true;post(JSON.stringify({text:v}),'application/json');
});
setMode('idle',cfg.start);
if(cfg.done){done=true;$('s').disabled=true;$('mic').disabled=true;$('mic').textContent=''}
$('hello').textContent=cfg.hello;$('q').textContent=cfg.prompt;$('or').textContent=cfg.or;$('s').textContent=cfg.send;
})();
</script></main></body></html>
"""


def page(client: dict) -> sitepage.Page:
    lang = client["lang"] if client["lang"] in STRINGS else "en"
    st = s(lang)
    cfg = {k: st[k] for k in ("hello", "start", "speaking", "listening", "resume", "silence", "busy", "or", "send", "heard", "mic", "error", "limit", "gone")}
    cfg.update(prompt=prompt_of(client), done=bool(client.get("onboarding_done")))
    blob = json.dumps(cfg, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    out = (_PAGE.replace("__LANG__", lang).replace("__TITLE__", html.escape(st["title"]))
           .replace("__BOT__", html.escape(config.BOT_NAME)).replace("__CFG__", blob))
    csp = sitepage.csp_for(out).replace("connect-src 'none'", "connect-src 'self'") \
        .replace("img-src 'self' data:", "img-src 'self' data:; media-src data:")
    return sitepage.Page(out, csp)


def gone_page() -> sitepage.Page:
    st = STRINGS["en"]
    body = "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>" \
           "<meta name=robots content=noindex><title>%s</title><p style='font:17px sans-serif;padding:24px'>%s<br>%s</p>" % (
               html.escape(st["title"]), html.escape(STRINGS["tr"]["gone"]), html.escape(st["gone"]))
    return sitepage.Page(body, "default-src 'none'; frame-ancestors 'none'")

"""HTML for the admin screens (Jinja2 with autoescape on, so names and notes can never inject markup).

Design: a quiet control room. Flat colour only (no gradients), one accent, a dark navigation rail, tabular numerals,
every state spelled out in words (not colour alone). No scripts: the page works under a strict content-security-policy.
"""
from __future__ import annotations

from jinja2 import DictLoader, Environment, select_autoescape

BASE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark"><meta name="robots" content="noindex">
<title>{{ bot }} · {{ title }}</title>
<style>
:root{--paper:#f3f1ec;--panel:#fbfaf7;--ink:#171612;--soft:#55524a;--faint:#8a867b;--rule:#ddd9cf;--rule2:#c9c4b7;
--rail:#171612;--rail-ink:#e9e6dc;--rail-soft:#9b978a;--acc:#0f5c47;--acc-ink:#ffffff;--acc-soft:#dcebe4;
--bad:#a5281f;--bad-soft:#f6dfdb;--warn:#7a4b00;--warn-soft:#f5e6c8;--ok:#0f5c47;
--r:6px;--fs:15px;color-scheme:light dark}
@media (prefers-color-scheme:dark){:root{--paper:#11110f;--panel:#191916;--ink:#ecebe4;--soft:#b3b0a4;--faint:#7f7c71;--rule:#2b2a25;--rule2:#3b3a33;
--rail:#0a0a09;--rail-ink:#ecebe4;--rail-soft:#85827a;--acc:#5fd0a3;--acc-ink:#07130e;--acc-soft:#173528;
--bad:#ff8f84;--bad-soft:#3a1c19;--warn:#f0bd62;--warn-soft:#352a12;--ok:#5fd0a3}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);font:var(--fs)/1.5 ui-sans-serif,-apple-system,"Segoe UI Variable","Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
font-variant-numeric:tabular-nums;scrollbar-color:var(--rule2) var(--paper);accent-color:var(--acc)}
::selection{background:var(--acc);color:var(--acc-ink)}
a{color:inherit;text-decoration-color:var(--rule2);text-underline-offset:3px}a:hover{text-decoration-color:currentColor}
:focus-visible{outline:2px solid var(--acc);outline-offset:2px;border-radius:3px}
.shell{display:grid;grid-template-columns:220px minmax(0,1fr);min-height:100vh}
.rail{background:var(--rail);color:var(--rail-ink);padding:22px 16px;position:sticky;top:0;height:100vh;display:flex;flex-direction:column;gap:22px}
.brand{font-weight:650;font-size:17px;letter-spacing:-.01em;text-decoration:none;display:block;padding:0 8px}
.brand small{display:block;font-weight:400;font-size:12px;color:var(--rail-soft);letter-spacing:0;margin-top:2px}
.nav{display:flex;flex-direction:column;gap:2px}
.nav a{display:block;padding:8px 10px;border-radius:var(--r);text-decoration:none;color:var(--rail-soft)}
.nav a:hover{color:var(--rail-ink);background:#ffffff14}
.nav a[aria-current=page]{color:var(--rail-ink);background:#ffffff1f;font-weight:600}
.rail .foot{margin-top:auto;font-size:12px;color:var(--rail-soft);padding:0 8px}
main{padding:30px clamp(16px,4vw,48px) 80px;max-width:1120px;width:100%;min-width:0}
.head{display:flex;align-items:baseline;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:22px}
h1{font-size:26px;line-height:1.15;letter-spacing:-.02em;margin:0;font-weight:650}
h2{font-size:12px;letter-spacing:.07em;text-transform:uppercase;color:var(--soft);font-weight:650;margin:34px 0 10px}
h3{font-size:15px;margin:0 0 4px;font-weight:650}
.lede{color:var(--soft);margin:6px 0 0;max-width:62ch}
.mute{color:var(--soft)}.faint{color:var(--faint)}.bad{color:var(--bad)}.warn{color:var(--warn)}.ok{color:var(--ok)}
.sec{background:var(--panel);border:1px solid var(--rule);border-radius:var(--r)}
.pad{padding:16px 18px}
.strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr))}
.strip>div{padding:14px 18px;border-right:1px solid var(--rule)}
.strip>div:last-child{border-right:0}
.strip .n{display:block;font-size:26px;font-weight:650;letter-spacing:-.02em;line-height:1.1}
.strip .l{font-size:13px;color:var(--soft)}
.two{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}
.alert{border:1px solid var(--rule2);border-radius:var(--r);background:var(--panel);margin-bottom:10px}
.alert header{padding:9px 16px;font-weight:650;display:flex;justify-content:space-between;gap:12px;border-bottom:1px solid var(--rule)}
.alert.bad header{background:var(--bad-soft);color:var(--bad)}.alert.warn header{background:var(--warn-soft);color:var(--warn)}.alert.ok header{background:var(--acc-soft);color:var(--ok)}
.alert .row{padding:10px 16px;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;border-bottom:1px solid var(--rule)}
.alert .row:last-child{border-bottom:0}
.scroll{overflow-x:auto}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:10px 14px;border-bottom:1px solid var(--rule);vertical-align:top}
tr:last-child td{border-bottom:0}
th{font-size:12px;font-weight:600;color:var(--soft);letter-spacing:.03em;white-space:nowrap;background:var(--panel)}
tbody tr:hover td{background:#8883}
td.num,th.num{text-align:right}
.tag{display:inline-block;padding:1px 8px;border:1px solid var(--rule2);border-radius:99px;font-size:12px;line-height:1.6;white-space:nowrap}
.tag.acc{border-color:var(--acc);color:var(--ok)}.tag.bad{border-color:var(--bad);color:var(--bad)}.tag.warn{border-color:var(--warn);color:var(--warn)}
label{display:block;font-size:13px;color:var(--soft);margin:12px 0 4px}
input[type=text],input[type=password],input[type=email],select{width:100%;padding:9px 11px;border:1px solid var(--rule2);border-radius:var(--r);background:var(--paper);color:var(--ink);font:inherit}
input::placeholder{color:var(--faint)}
.fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:0 14px}
button{padding:8px 14px;border-radius:var(--r);border:1px solid var(--rule2);background:var(--panel);color:var(--ink);font:inherit;cursor:pointer;font-weight:550}
button:hover{border-color:var(--ink)}
button.primary{background:var(--acc);border-color:var(--acc);color:var(--acc-ink)}button.primary:hover{filter:brightness(1.08)}
button.danger{color:var(--bad);border-color:var(--bad);background:transparent}
form.inline{display:inline}
.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
.switch{display:flex;align-items:center;gap:12px;margin:12px 0 4px;font-size:15px;color:var(--ink)}
.switch input{appearance:none;-webkit-appearance:none;width:42px;height:24px;border-radius:99px;background:var(--rule2);position:relative;cursor:pointer;margin:0;flex:none;transition:background .15s}
.switch input::after{content:"";position:absolute;top:3px;left:3px;width:18px;height:18px;border-radius:50%;background:var(--panel);transition:transform .15s}
.switch input:checked{background:var(--acc)}.switch input:checked::after{transform:translateX(18px);background:var(--acc-ink)}
.switch input:focus-visible{outline:2px solid var(--acc);outline-offset:3px}
@media (prefers-reduced-motion:reduce){.switch input,.switch input::after{transition:none}}
.sub{display:flex;gap:4px;flex-wrap:wrap;margin:0 0 4px;padding:0;list-style:none;font-size:13px}
.sub a{display:block;padding:5px 11px;border:1px solid var(--rule);border-radius:99px;text-decoration:none;color:var(--soft)}
.sub a:hover{border-color:var(--ink);color:var(--ink)}
.kv{display:grid;grid-template-columns:max-content 1fr;gap:6px 18px;margin:0}.kv dt{color:var(--soft)}.kv dd{margin:0}
ul.facts{margin:4px 0 12px;padding-left:18px}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.92em}
.note{font-size:13px;color:var(--soft);margin:8px 0 0;max-width:70ch}
@media (max-width:860px){
.shell{grid-template-columns:minmax(0,1fr)}
.rail{position:static;height:auto;flex-direction:row;align-items:center;gap:12px;padding:12px 16px;flex-wrap:wrap}
.brand small,.rail .foot{display:none}.nav{flex-direction:row;gap:2px}
main{padding-top:22px}
.strip>div{border-right:0;border-bottom:1px solid var(--rule)}.strip>div:last-child{border-bottom:0}
}
</style></head><body>
<div class="shell">
<aside class="rail"><a class="brand" href="/admin">{{ bot }}<small>Control room</small></a>
<nav class="nav" aria-label="Main">
<a href="/admin" {{ 'aria-current=page' if nav == 'home' }}>Overview</a>
<a href="/admin/referrals" {{ 'aria-current=page' if nav == 'referrals' }}>Referrals</a>
<a href="/admin/settings" {{ 'aria-current=page' if nav == 'settings' }}>Settings</a>
<a href="/" target="_blank" rel="noopener">Website</a>
</nav>
<div class="foot">Client messages are never shown here, only the profile the assistant keeps.</div></aside>
<main>
{% block body %}{% endblock %}
</main></div></body></html>"""

DASHBOARD = """{% extends "base" %}{% block body %}
<div class="head"><div><h1>Overview</h1><p class="lede">{{ clients|length }} client{{ '' if clients|length == 1 else 's' }}. Calling is <b>{{ 'on' if calling_on else 'off' }}</b>.</p></div></div>

{% if problems or frozen or upgrades or reviews or dead or events %}
{% if problems %}<section class="alert bad" aria-label="Problems"><header>Needs your attention</header>
{% for p in problems %}<div class="row"><span>{{ p }}</span></div>{% endfor %}</section>{% endif %}
{% if upgrades %}<section class="alert ok" aria-label="Plus requests"><header>Asked for Plus <span>{{ upgrades|length }}</span></header>
{% for u in upgrades %}<div class="row"><span>{{ u.name or u.phone }} <span class="faint">{{ u.phone }} · {{ u.at|when }} UTC</span></span>
<form class="inline" method="post" action="/admin/upgrades/{{ u.id }}/done"><input type="hidden" name="csrf" value="{{ csrf }}"><button>Mark as handled</button></form></div>{% endfor %}</section>{% endif %}
{% if reviews %}<section class="alert warn" aria-label="Receipts to check"><header>Receipts to check <span>{{ reviews|length }}</span></header>
{% for r in reviews %}<div class="row"><span>{{ r.name or r.phone }} <span class="faint">{{ r.plan or '?' }} · {{ r.amount_tl }} TL · {{ r.at|when }} UTC · check your bank app first</span></span>
<span><form class="inline" method="post" action="/admin/payments/{{ r.id }}/approve"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="primary">Money arrived: apply</button></form>
<form class="inline" method="post" action="/admin/payments/{{ r.id }}/reject"><input type="hidden" name="csrf" value="{{ csrf }}"><button>Reject</button></form></span></div>{% endfor %}</section>{% endif %}
{% if frozen %}<section class="alert bad" aria-label="Frozen clients"><header>Frozen clients <span>{{ frozen|length }}</span></header>
{% for c in frozen %}<div class="row"><span><a href="/admin/clients/{{ c.id }}">{{ c.name or c.phone }}</a> <span class="faint">{{ c.frozen_reason }} · since {{ c.frozen_at|when }} UTC</span></span></div>{% endfor %}</section>{% endif %}
{% if dead %}<section class="alert warn" aria-label="Failed messages"><header>Failed messages <span>{{ dead|length }}</span></header>
{% for j in dead %}<div class="row"><span>#{{ j.id }} {{ j.kind }} · …{{ j.phone[-4:] }} · {{ j.attempts }} tries <span class="faint">{{ j.last_error }}</span></span>
<form class="inline" method="post" action="/admin/jobs/{{ j.id }}/retry"><input type="hidden" name="csrf" value="{{ csrf }}"><button>Retry</button></form></div>{% endfor %}
<div class="row"><span class="faint">Fix the cause first (for example a new access token in .env, then restart), then press Retry.</span></div></section>{% endif %}
{% endif %}

<h2>Queue</h2>
<div class="sec strip">
{% for name, label in [("pending","Waiting"),("running","Running"),("done","Done"),("dead","Failed")] %}
<div><span class="n {{ 'bad' if name=='dead' and counts[name] else '' }}">{{ counts[name] }}</span><span class="l">{{ label }}</span></div>{% endfor %}
</div>

<h2>Free trial funnel</h2>
<div class="sec strip">
<div><span class="n">{{ funnel.waiting }}</span><span class="l">Waiting for first message</span></div>
<div><span class="n">{{ funnel.running }}</span><span class="l">Trial running</span></div>
<div><span class="n">{{ funnel.ended }}</span><span class="l">Ended, still Basic</span></div>
<div><span class="n">{{ funnel.converted }}</span><span class="l">Moved to Plus</span></div>
<div><span class="n">{{ (funnel.rate ~ '%') if funnel.rate is not none else '-' }}</span><span class="l">Of started trials</span></div>
</div>

<h2>Add a client</h2>
<form class="sec pad" method="post" action="/admin/clients">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <div class="fields">
    <div><label for="phone">WhatsApp number with country code</label><input id="phone" type="text" name="phone" placeholder="+90 555 123 45 67" required autocomplete="off"></div>
    <div><label for="name">Name</label><input id="name" type="text" name="name" placeholder="Mehmet Yılmaz" maxlength="80" autocomplete="off"></div>
    <div><label for="plan">Plan</label><select id="plan" name="plan"><option value="lite">Lite (chat only)</option><option value="basic">Basic (starts with a free Plus trial)</option><option value="plus">Plus</option></select></div>
  </div>
  <p class="note">The first message from this number starts the short setup questions automatically.</p>
  <div class="actions"><button class="primary">Add client</button></div>
</form>

<h2>Clients</h2>
<div class="sec scroll">
{% if clients %}<table>
<thead><tr><th>Name</th><th>Number</th><th>Plan</th><th>Status</th><th>Setup</th><th class="num">Messages</th><th class="num">Tokens</th><th class="num">Est. cost</th><th>Mail</th></tr></thead><tbody>
{% for c in clients %}<tr>
  <td><a href="/admin/clients/{{ c.id }}">{{ c.name or "(no name)" }}</a></td>
  <td>{{ c.phone }}</td>
  <td><span class="tag {{ 'acc' if c.effective == 'plus' }}">{{ c.plan }}</span>{% if c.free %} <span class="tag">free</span>{% endif %}{% if c.trial_ends_at and c.plan != "plus" %} <span class="tag">{{ 'trial' if c.effective == 'plus' else 'trial ended' }}</span>{% endif %}</td>
  <td>{% if c.active %}<span class="tag acc">active</span>{% else %}<span class="tag warn">paused</span>{% endif %}{% if c.frozen %} <span class="tag bad">frozen</span>{% endif %}</td>
  <td>{% if c.onboarding_done %}done{% else %}<span class="warn">step {{ c.onboarding_step }}/{{ c.total_q }}</span>{% endif %}</td>
  <td class="num">{{ c.msgs }}</td><td class="num">{{ "{:,}".format(c.tokens) }}</td>
  <td class="num">{{ c.cost.total_txt }}{% if c.cost.pct is not none %} <span class="faint">({{ c.cost.pct }}% of fee)</span>{% endif %}</td>
  <td>{% if c.mail_on %}<span class="tag acc">connected</span>{% else %}<span class="faint">no</span>{% endif %}</td>
</tr>{% endfor %}
</tbody></table>{% else %}<div class="pad mute">No clients yet. Add your own number first to try it.</div>{% endif %}
</div>
<p class="note">Messages, tokens and cost are for the current month.</p>
<p class="note"><b>Estimated cost this month, all clients: {{ cost_total }}{% if cost_total_tl %} (about {{ cost_total_tl }}){% endif %}.</b> An estimate from the counters and the rates in <code>.env</code>, not an invoice. Check the provider dashboards for the real numbers.</p>

{% if events %}<h2>Recent events</h2>
<div class="sec scroll"><table><tbody>{% for e in events %}<tr><td class="faint" style="white-space:nowrap">{{ e.at|when }}</td><td>{{ e.text }}</td></tr>{% endfor %}</tbody></table></div>{% endif %}
{% endblock %}"""

DETAIL = """{% extends "base" %}{% block body %}
<p class="mute" style="margin:0 0 10px"><a href="/admin">Overview</a> / Client</p>
<div class="head"><div><h1>{{ c.name or "(no name)" }}</h1><p class="lede">{{ c.phone }}</p></div>
<div>
  <span class="tag {{ 'acc' if c.active else 'warn' }}">{{ "active" if c.active else "paused" }}</span>
  <span class="tag">language {{ c.lang }}</span>
  <span class="tag">setup {{ "done" if c.onboarding_done else "step %d/%d"|format(c.onboarding_step, total_q) }}</span>
  <span class="tag">24 h window {{ "open" if window else "closed" }}</span>
  {% if c.frozen %}<span class="tag bad">FROZEN</span>{% endif %}
</div></div>
<ul class="sub" aria-label="Sections"><li><a href="#plan">Plan</a></li><li><a href="#mail">Mail</a></li><li><a href="#calls">Calls</a></li><li><a href="#usage">Usage</a></li><li><a href="#memory">Memory</a></li><li><a href="#stocks">Stocks</a></li><li><a href="#privacy">Privacy</a></li></ul>

<div class="actions" style="margin-top:14px">
  <form class="inline" method="post" action="/admin/clients/{{ c.id }}/toggle"><input type="hidden" name="csrf" value="{{ csrf }}"><button>{{ "Pause" if c.active else "Resume" }}</button></form>
  <form class="inline" method="post" action="/admin/clients/{{ c.id }}/ping"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="primary">Send test ping</button></form>
</div>
<p class="note">The window is open for 24 hours after the client's last message. Outside it only approved templates can be sent.</p>

{% if c.frozen %}
<div class="alert bad" style="margin-top:18px"><header>Frozen: {{ c.frozen_reason }}</header>
<div class="row"><span>Since {{ c.frozen_at|when }} UTC. They get one notice a day and nothing else is processed (reminders still arrive).</span>
<form class="inline" method="post" action="/admin/clients/{{ c.id }}/unfreeze"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="primary">Unfreeze</button></form></div></div>
{% endif %}

<h2 id="plan">Plan and trial</h2>
<div class="two">
<form class="sec pad" method="post" action="/admin/clients/{{ c.id }}/plan">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <h3>Plan</h3>
  <label for="plan">Plan</label><select id="plan" name="plan">
    <option value="lite" {{ "selected" if plan == "lite" }}>Lite</option>
    <option value="basic" {{ "selected" if plan == "basic" }}>Basic</option>
    <option value="plus" {{ "selected" if plan == "plus" }}>Plus</option></select>
  <label for="wa">Field of work (briefing news)</label><input id="wa" type="text" name="work_area" value="{{ c.work_area }}" maxlength="80">
  <label for="bt">Morning briefing time (Plus, HH:MM, empty keeps it)</label><input id="bt" type="text" name="briefing_time" value="{{ briefing_time }}" placeholder="08:30" maxlength="5">
  <div class="actions"><button class="primary">Save plan</button></div>
</form>
<form class="sec pad" method="post" action="/admin/clients/{{ c.id }}/trial">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <h3>Free Plus trial</h3>
  <p class="mute" style="margin:0">
  {% if trial.active %}Running until <b>{{ trial.ends }}</b>.{% elif trial.ends %}Ended {{ trial.ends }}.{% elif trial.pending %}Starts with their first message ({{ trial.pending }} days).{% else %}None.{% endif %}</p>
  <label for="td">Start a trial from now (days, 0 removes it)</label>
  <input id="td" type="text" name="days" inputmode="numeric" value="7" maxlength="2" style="max-width:110px">
  <div class="actions"><button>Set trial</button></div>
</form>
</div>

<h2 id="payment">Payment</h2>
<div class="two">
<form class="sec pad" method="post" action="/admin/clients/{{ c.id }}/paid">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <h3>Mark as paid</h3>
  <p class="mute" style="margin:0">{% if pay.free %}Free (family): never billed.{% elif pay.due %}{{ 'Paid until' if pay.paid_until else 'Trial ends' }} <b>{{ pay.due }}</b>{% if pay.grace %} · extra time while a receipt is checked{% endif %}{% if pay.unpaid_freeze %} · <b>frozen: unpaid</b>{% endif %}.{% else %}No due date yet.{% endif %}
  Next payment: Basic <b>{{ pay.next_basic }} TL</b>, Plus <b>{{ pay.next_plus }} TL</b>. Their code: <b>{{ pay.code or 'made on first use' }}</b>.</p>
  <label for="pp">Plan</label><select id="pp" name="plan"><option value="lite" {{ "selected" if plan == "lite" }}>Lite</option><option value="basic" {{ "selected" if plan == "basic" }}>Basic</option><option value="plus" {{ "selected" if plan == "plus" }}>Plus</option></select>
  <label for="pm">Months</label><input id="pm" type="text" name="months" inputmode="numeric" value="1" maxlength="2" style="max-width:110px">
  <label for="pa">Amount received in TL (blank = the normal price)</label><input id="pa" type="text" name="amount" inputmode="decimal" maxlength="12" style="max-width:160px">
  <div class="actions"><button class="primary">Mark as paid</button></div>
</form>
<form class="sec pad" method="post" action="/admin/clients/{{ c.id }}/free">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <h3>Family and friends</h3>
  <p class="mute" style="margin:0">A free client is never reminded, never frozen for payment and never asked to pay.</p>
  <input type="hidden" name="on" value="{{ '0' if pay.free else '1' }}">
  <div class="actions"><button>{{ 'Make this client paying again' if pay.free else 'Mark as free (family)' }}</button></div>
</form>
</div>
{% if pay.payments %}<div class="sec scroll"><table><thead><tr><th scope="col">When</th><th scope="col">Plan</th><th scope="col">Months</th><th scope="col">TL</th><th scope="col">How</th><th scope="col">Status</th></tr></thead><tbody>
{% for p in pay.payments %}<tr><td class="faint">{{ p.at|when }}</td><td>{{ p.plan }}</td><td>{{ p.months }}</td><td>{{ p.amount_tl }}</td><td>{{ p.source }}</td><td><span class="tag">{{ p.status }}</span></td></tr>{% endfor %}</tbody></table></div>{% endif %}
{% if not pay.on %}<p class="note">Billing is OFF (BILLING_ON in .env): marking paid still records it, but no reminders are sent and nobody is frozen.</p>{% endif %}

<h2 id="mail">Email</h2>
<div class="sec pad">
{% if not mail_available %}
  <p class="warn" style="margin:0"><b>Mail is not switched on.</b> Set <span class="mono">MAIL_MASTER_KEY</span> in .env (README: “Email setup”) and restart.</p>
{% elif mail %}
  <dl class="kv"><dt>Connected</dt><dd><b>{{ mail.address }}</b> since {{ mail.since[:10] }}</dd>
  <dt>Folder</dt><dd>{{ mail.folder }}</dd><dt>Only these senders</dt><dd>{{ mail.senders or "anyone (no limit)" }}</dd>
  <dt>Key</dt><dd>{% if mail.key_ok %}<span class="ok">sealed, key available</span>{% else %}<span class="bad">sealed with a key that is not in .env</span>{% endif %}</dd></dl>
  <form method="post" action="/admin/clients/{{ c.id }}/mail/scope"><input type="hidden" name="csrf" value="{{ csrf }}">
    <div class="fields"><div><label for="mf">Folder or label the assistant may read</label><input id="mf" type="text" name="folder" value="{{ mail.folder }}"></div>
    <div><label for="ms">Only these senders or domains (comma separated, empty = anyone)</label><input id="ms" type="text" name="senders" value="{{ mail.senders }}" placeholder="bank.com, ayse@firma.com"></div></div>
    <div class="actions"><button>Save limits</button></div></form>
  <form method="post" action="/admin/clients/{{ c.id }}/mail/disconnect" class="actions"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="danger">Disconnect and delete the stored login</button></form>
  {% if mail_history %}<h3 style="margin-top:18px">Recent access (no contents are stored)</h3>
  <div class="scroll"><table><tbody>{% for h in mail_history %}<tr><td class="faint" style="white-space:nowrap">{{ h.at|when }}</td><td>{{ h.action }}</td><td class="faint">{{ h.detail }}</td></tr>{% endfor %}</tbody></table></div>{% endif %}
{% else %}
  <p class="mute" style="margin:0 0 6px">Not connected. Connect it here with the client (the password never has to pass through WhatsApp). Use an app password, not their normal password.</p>
  <form method="post" action="/admin/clients/{{ c.id }}/mail/connect"><input type="hidden" name="csrf" value="{{ csrf }}">
    <div class="fields">
      <div><label for="ma">Email address</label><input id="ma" type="email" name="address" autocomplete="off" required></div>
      <div><label for="mp">App password</label><input id="mp" type="password" name="password" autocomplete="new-password" required></div>
      <div><label for="mf2">Folder or label (optional)</label><input id="mf2" type="text" name="folder" value="INBOX"></div>
      <div><label for="ms2">Only these senders or domains (optional)</label><input id="ms2" type="text" name="senders" placeholder="bank.com"></div>
    </div>
    <p class="note">Google: myaccount.google.com/apppasswords (2-step verification must be on). The login is tested, then stored sealed; it is shown nowhere afterwards.</p>
    <div class="actions"><button class="primary">Test and connect</button></div></form>
{% endif %}
</div>

<h2 id="calls">Calls</h2>
<form class="sec pad" method="post" action="/admin/clients/{{ c.id }}/calls">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <label class="switch"><input type="checkbox" name="on" value="1" {{ "checked" if c.calls_on }}> Calling add-on for this client</label>
  <p class="note">{% if calling_on %}The master switch is on.{% else %}The master switch (Settings) is off, so nothing can be called yet, whatever you choose here.{% endif %} Needs the Plus plan.</p>
  <div class="actions"><button>Save</button></div>
</form>

<h2 id="usage">Usage</h2>
<div class="sec">
  <div class="scroll"><table><thead><tr><th>This month ({{ month }})</th><th class="num">Used</th><th class="num">Allowed</th></tr></thead><tbody>
  {% for u in usage %}<tr><td>{{ u.key }}</td><td class="num {{ 'bad' if u.limit is not none and u.used >= u.limit else '' }}">{{ u.used }}</td><td class="num">{{ u.limit if u.limit is not none else "no limit" }}</td></tr>{% endfor %}
  </tbody></table></div>
  <div class="scroll" style="border-top:1px solid var(--rule)"><table><thead><tr><th>Estimated cost this month</th><th class="num">Amount</th><th class="num">Cost</th></tr></thead><tbody>
  {% for r in cost.rows %}<tr><td>{{ r.label }}</td><td class="num">{{ "{:,}".format(r.qty) }}</td><td class="num">{{ r.usd_txt }}</td></tr>{% endfor %}
  <tr><td><b>Total</b></td><td></td><td class="num"><b>{{ cost.total_txt }}</b>{% if cost.tl_txt %} <span class="faint">({{ cost.tl_txt }}{% if cost.pct is not none %}, {{ cost.pct }}% of fee{% endif %})</span>{% endif %}</td></tr>
  </tbody></table></div>
  <div class="pad" style="border-top:1px solid var(--rule)">
  <b>Last hour:</b> {{ hour.messages }} messages, {{ hour.huge }} huge prompts (over {{ abuse.huge_chars }} characters), {{ "{:,}".format(hour.tokens) }} AI tokens.
  <span class="mute">Auto-freeze at {{ abuse.huge_per_hour }} huge prompts or {{ "{:,}".format(abuse.tokens_per_hour) }} tokens within an hour.</span>
  {% if not c.frozen %}<div class="actions"><form method="post" action="/admin/clients/{{ c.id }}/freeze"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="danger">Freeze this client</button></form></div>{% endif %}
  </div>
</div>

<h2 id="memory">What the assistant knows</h2>
<div class="sec pad">
{% set any = namespace(v=false) %}
{% for section, items in memory.items() %}{% if items %}{% set any.v = true %}
  <div><b>{{ section }}</b><ul class="facts">{% for i in items %}<li>{{ i.text }} <span class="faint">({{ i.added }})</span></li>{% endfor %}</ul></div>
{% endif %}{% endfor %}
{% if not any.v %}<span class="mute">Nothing saved yet.</span>{% endif %}
</div>

<h2 id="stocks">Stock watchlist</h2>
<div class="two">
<div class="sec scroll">
{% if watches %}<table><thead><tr><th>Symbol</th><th class="num">Upper</th><th class="num">Lower</th><th class="num">Move %</th><th>State</th></tr></thead><tbody>
{% for w in watches %}<tr><td>{{ w.symbol }}</td><td class="num">{{ w.upper if w.upper is not none else "-" }}</td>
<td class="num">{{ w.lower if w.lower is not none else "-" }}</td><td class="num">{{ w.move_pct if w.move_pct is not none else "-" }}</td><td>{{ w.state }}</td></tr>{% endfor %}
</tbody></table>{% else %}<div class="pad mute">No stocks watched. The client sets these by chatting, e.g. “THYAO üst 320, alt 290”.</div>{% endif %}
</div>

</div>

<h2 id="privacy">Privacy</h2>
<div class="two">
  <form class="sec pad" method="post" action="/admin/clients/{{ c.id }}/clear-memory">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <h3>Clear memory</h3><p class="note" style="margin-top:0">Deletes the memory file only. Chat history, reminders and stocks stay (reminders are never shown here).</p>
    <div class="actions"><button class="danger">Clear memory</button></div>
  </form>
  <form class="sec pad" method="post" action="/admin/clients/{{ c.id }}/delete">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <h3>Delete client and all data</h3>
    <p class="note" style="margin-top:0">Removes messages, reminders, stocks, mail login, queued jobs and memory. This cannot be undone. Type the last 4 digits ({{ c.phone[-4:] }}) to confirm.</p>
    <label for="cf">Last 4 digits</label><input id="cf" type="text" name="confirm" inputmode="numeric" maxlength="4" autocomplete="off" required style="max-width:120px">
    <div class="actions"><button class="danger">Delete everything</button></div>
  </form>
</div>
{% endblock %}"""

REFERRALS = """{% extends "base" %}{% block body %}
<div class="head"><div><h1>Referrals</h1><p class="lede">Who invited whom. A reward is earned only after the friend's first real payment.</p></div></div>
<p class="note">Automatic sign-ups today: {{ today }} of {{ cap }}. Friend discount: {{ friend_pct }}% on the first month. Ladder (friend number from : %): {{ ladder }}.</p>
{% if rows %}<div class="sec scroll"><table><thead><tr><th scope="col">Inviter</th><th scope="col">Number</th><th scope="col">Code</th><th class="num" scope="col">Friends</th><th class="num" scope="col">Paid</th><th class="num" scope="col">Pending discount</th></tr></thead><tbody>
{% for r in rows %}<tr><td>{{ r.name or "(no name)" }}</td><td>{{ r.phone }}</td><td>{{ r.code }}</td><td class="num">{{ r.friends }}</td><td class="num">{{ r.paid }}</td><td class="num">{{ r.pending }}%</td></tr>{% endfor %}</tbody></table></div>
{% else %}<p class="note">No referrals yet.</p>{% endif %}
{% endblock %}"""

SETTINGS = """{% extends "base" %}{% block body %}
<div class="head"><div><h1>Settings</h1><p class="lede">Switches that take effect straight away, no restart needed.</p></div></div>
<ul class="sub" aria-label="Sections"><li><a href="#contact">Contact number</a></li><li><a href="#calling">Calling</a></li><li><a href="#mail">Mail security</a></li><li><a href="#payments">Payments</a></li><li><a href="#backups">Backups</a></li><li><a href="#events">Events</a></li></ul>

<h2 id="contact">Your phone number</h2>
<form class="sec pad" method="post" action="/admin/settings/contact">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <label class="switch"><input type="checkbox" name="on" value="1" {{ "checked" if contact_on }}> Show my number to clients</label>
  <p class="note">Off: the frozen-account notice and the trial messages tell clients to write or contact you, with no phone number and no promise of a call. On: they get the number below and the afternoon call-back wording.</p>
  <div class="fields">
    <div><label for="cp">Number to show clients</label><input id="cp" type="text" name="number" value="{{ contact_phone }}" placeholder="+90 555 123 45 67"></div>
    <div><label for="ap">Private number for alerts (optional)</label><input id="ap" type="text" name="alert" value="{{ alert_phone }}" placeholder="+90 555 123 45 67"></div>
  </div>
  <p class="note">The alert number gets a WhatsApp message when something breaks (stuck queue, failed jobs, failed backup). It must have messaged the bot in the last 24 hours. Empty: alerts only appear here.</p>
  <div class="actions"><button class="primary">Save</button></div>
</form>

<h2 id="calling">Calling add-on</h2>
<form class="sec pad" method="post" action="/admin/settings/calling">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <label class="switch"><input type="checkbox" name="on" value="1" {{ "checked" if calling_on }}> Calling is available</label>
  <p class="note">Off by default. When on, only clients with their own “Calls” switch (client page) and the Plus plan can use it. Every call is confirmed by the client first, limited to {{ calls_per_day }} a day, and logged below.</p>
  <div class="fields">
    <div><label for="pv">Provider</label><select id="pv" name="provider">
      <option value="test" {{ "selected" if provider == "test" }}>Test mode (never dials, only logs)</option>
      <option value="webhook" {{ "selected" if provider == "webhook" }}>Webhook (your calling service)</option></select></div>
    <div><label for="cu">Webhook address (https)</label><input id="cu" type="text" name="url" value="{{ calling_url }}" placeholder="https://..."></div>
    <div><label for="ck">Webhook key {{ "(saved, leave empty to keep)" if calling_key_set }}</label><input id="ck" type="password" name="key" autocomplete="new-password"></div>
  </div>
  <p class="note">The webhook receives a JSON POST with <span class="mono">to</span>, <span class="mono">message</span>, <span class="mono">language</span> and <span class="mono">client</span>, and an <span class="mono">Authorization: Bearer</span> header. The key is stored sealed.</p>
  <div class="actions"><button class="primary">Save</button></div>
</form>
{% if calls %}<div class="sec scroll" style="margin-top:12px"><table><thead><tr><th>When (UTC)</th><th>Client</th><th>To</th><th>Result</th><th>Detail</th></tr></thead><tbody>
{% for k in calls %}<tr><td style="white-space:nowrap">{{ k.at|when }}</td><td>{{ k.name or k.phone }}</td><td>{{ k.to_number }}</td><td><span class="tag {{ 'acc' if k.status in ('placed','test') else 'bad' }}">{{ k.status }}</span></td><td class="faint">{{ k.detail }}</td></tr>{% endfor %}</tbody></table></div>{% endif %}

<h2 id="mail">Mail security</h2>
<div class="sec pad">
  <dl class="kv">
    <dt>Master key</dt><dd>{% if vault_ok %}<span class="ok">present</span> <span class="faint">(id {{ key_id }})</span>{% else %}<span class="warn">missing: email and the webhook key are switched off</span>{% endif %}</dd>
    <dt>Connected mailboxes</dt><dd>{{ mail_accounts }}</dd>
    <dt>Sending route</dt><dd>{% if mail_relay %}<span class="ok">relay</span> <span class="faint">({{ mail_relay }})</span>{% else %}direct from this server <span class="faint">(blocked on DigitalOcean; see relay/README.md)</span>{% endif %}</dd>
  </dl>
  {% if not vault_ok %}<p class="note">Make a key on the server with <span class="mono">python3 -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"</span>, put it in .env as <span class="mono">MAIL_MASTER_KEY=…</span> and restart. Keep a copy somewhere that is not the server. Without it the sealed logins cannot be opened again.</p>{% endif %}
  <form method="post" action="/admin/settings/rewrap"><input type="hidden" name="csrf" value="{{ csrf }}">
    <p class="note" style="margin-top:12px">After changing the key (new one in MAIL_MASTER_KEY, old one in MAIL_MASTER_KEY_OLD), press this once to re-seal every login with the new key.</p>
    <div class="actions"><button>Re-seal logins with the current key</button></div></form>
</div>

<h2 id="backups">Backups</h2>
<div class="sec pad">
  <p class="mute" style="margin:0">A copy of the database is made every night at 03:40 into <span class="mono">{{ backup_dir }}</span>. The key in .env is not inside it. Copy this folder off the server too.</p>
  <div class="actions"><form method="post" action="/admin/settings/backup" class="inline"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="primary">Back up now</button></form></div>
  {% if backups %}<div class="scroll"><table style="margin-top:12px"><tbody>{% for b in backups %}<tr><td class="mono">{{ b.name }}</td><td class="num faint">{{ (b.size / 1024)|round|int }} KB</td></tr>{% endfor %}</tbody></table></div>
  {% else %}<p class="note">No backups yet.</p>{% endif %}
</div>

<h2 id="payments">Payments</h2>
<div class="sec pad">
  <p class="mute" style="margin:0">Billing is <b>{{ 'ON' if billing.on else 'OFF' }}</b>{% if billing.on and not billing.bank_ok %} <span class="tag">bank details missing: nothing is sent</span>{% endif %}. Set BILLING_ON, BANK_IBAN, BANK_HOLDER and BANK_NAME in .env, then restart.</p>
  <p class="mute">Account: {{ billing.holder or '(not set)' }} · {{ billing.iban or '(no IBAN)' }}</p>
  <table><thead><tr><th scope="col">Plan</th><th scope="col">Setup</th><th scope="col">Per month</th></tr></thead><tbody>
  {% for name, setup, month in billing.prices %}<tr><td>{{ name }}</td><td>{{ setup }} TL</td><td>{{ month }} TL ({{ billing.half_percent }}% for the first {{ billing.half_months }} paid months)</td></tr>{% endfor %}</tbody></table>
</div>

<h2 id="events">Recent events</h2>
<div class="sec scroll">
{% if events %}<table><tbody>{% for e in events %}<tr><td class="faint" style="white-space:nowrap">{{ e.at|when }} UTC</td><td><span class="tag">{{ e.kind }}</span></td><td>{{ e.text }}</td></tr>{% endfor %}</tbody></table>
{% else %}<div class="pad mute">Nothing has gone wrong.</div>{% endif %}
</div>
<p class="note">New clients on Basic get a free {{ trial_days }}-day Plus trial (TRIAL_DAYS in .env).</p>
{% endblock %}"""

_env = Environment(loader=DictLoader({"base": BASE, "dashboard": DASHBOARD, "detail": DETAIL, "settings": SETTINGS, "referrals": REFERRALS}),
                   autoescape=select_autoescape(default=True, default_for_string=True))
_env.filters["when"] = lambda v: (v or "")[:16].replace("T", " ")
_TITLES = {"dashboard": ("Overview", "home"), "detail": ("Client", "home"), "settings": ("Settings", "settings"), "referrals": ("Referrals", "referrals")}


def render(name: str, **ctx) -> str:
    title, nav = _TITLES.get(name, (name, ""))
    return _env.get_template(name).render(title=title, nav=nav, **ctx)

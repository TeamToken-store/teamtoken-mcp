"""The page a HUMAN sees after opening the server address in a browser.

That address is handed out in configs and in registries, so sooner or later
someone opens it by hand — and until now the answer was the string
`{"error":"unauthorized"}`. Correct and entirely useless: you cannot tell from
it whether something is broken or working as intended.

⚠️ **Only the response body changes.** The status stays 401 and the
`WWW-Authenticate` header stays with it: a connection starts from that pair
(RFC 9728 — a client reads 401 as "begin authorization"). Answering 200 with a
pretty page would trade a working connector for the look of it in a browser,
because Claude says plainly that it does not read `WWW-Authenticate` on a 200.

The two are told apart by `Accept`: a browser asks for `text/html`, an MCP
client for `application/json, text/event-stream`. The language follows
`Accept-Language`, because the address lives in Russian and English configs
alike.
"""
from __future__ import annotations

RU = {
    "lead": "Это машинный адрес MCP-сервера TeamToken. Браузеру здесь показывать нечего — "
            "адрес вставляют в конфиг клиента, а не открывают. Технически это ответ 401 — так и "
            "задумано: сервер сообщает клиенту, что нужна авторизация, и клиент начинает её сам.",
    "way1_t": "Вход через браузер — без ключа",
    "way1": "Оставьте конфиг без учётных данных вовсе: сервер ответит 401 и скажет, где лежат его "
            "метаданные авторизации, а клиент, который умеет MCP OAuth, дальше сам откроет браузер. "
            "Так работает Claude Code. В claude.ai и Claude Desktop то же самое делается формой: "
            "Настройки → Connectors → Add custom connector, вставьте адрес и нажмите Connect.",
    "way2_t": "Или ключом в заголовке",
    "way2": "Для клиентов, которые OAuth не умеют, и для тех, кому нужна постоянная учётка. Ключ "
            "берётся на странице «Ключи» в кабинете. Клиента нет во вкладках? Ему довольно адреса "
            "выше и заголовка <code>x-api-key</code> — или <code>Authorization: Bearer</code>, если "
            "своего заголовка он не умеет.",
    "cfg_t": "Готовые конфиги",
    "copy": "Скопировать",
    "copied": "Скопировано",
    "selected": "Выделено — нажмите ⌘C",
    "tools_t": "Что умеет",
    "tools": "Генерация картинок и видео, редактирование картинок, продление видео, список "
             "моделей с ценами и проверка баланса — ассистент вызывает это сам.",
    "docs": "Документация",
    "keys": "Получить ключ",
}
EN = {
    "lead": "This is the machine address of the TeamToken MCP server. There is nothing to see in "
            "a browser — the address goes into a client config. Technically this is a 401, and "
            "that is intended: it tells a client to start authorization, which it then does.",
    "way1_t": "Sign in through the browser — no key",
    "way1": "Leave the credential out of the config entirely: the server answers 401 and names "
            "where its authorization metadata lives, and a client that speaks MCP OAuth takes it "
            "from there and opens a browser. Claude Code works this way. claude.ai and Claude "
            "Desktop reach the same place through their form: Settings → Connectors → Add custom "
            "connector, paste the address, press Connect.",
    "way2_t": "Or a key in a header",
    "way2": "For clients that do not speak OAuth, and for anyone who wants a fixed credential. Get "
            "the key on the “Keys” page in your cabinet. Client not in the tabs? All it needs is "
            "the address above and an <code>x-api-key</code> header — or "
            "<code>Authorization: Bearer</code> if it cannot set its own.",
    "cfg_t": "Ready-made configs",
    "copy": "Copy",
    "copied": "Copied",
    "selected": "Selected — press ⌘C",
    "tools_t": "What it does",
    "tools": "Generate images and video, edit images, extend video, list models with prices and "
             "check the balance — the assistant calls these itself.",
    "docs": "Documentation",
    "keys": "Get a key",
}

_CSS = """
*{box-sizing:border-box}
body{margin:0;background:#0a0c10;color:#e8eaed;
     font:15px/1.6 'IBM Plex Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
.wrap{max-width:680px;margin:0 auto;padding:48px 20px 64px}
.head{display:flex;align-items:center;gap:14px;margin-bottom:22px}
.head img{width:52px;height:52px;border-radius:12px;flex:none}
h1{font-size:21px;margin:0;font-weight:600}
.addr{font-family:'IBM Plex Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
      font-size:13.5px;color:#7eb0ff;margin-top:3px;word-break:break-all}
p{color:#b7bcc7;margin:0 0 18px}
h2{font-size:14px;margin:26px 0 8px;font-weight:600}
.tabs{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:10px}
.tab{background:none;border:1px solid #1c222b;color:#8b90a0;border-radius:7px;padding:6px 11px;
     font:inherit;font-size:12.5px;cursor:pointer}
.tab[aria-selected="true"]{background:#11151c;color:#e8eaed;border-color:#25415f}
.copy{margin-left:auto;background:#11151c;border:1px solid #1c222b;
      color:#b7bcc7;border-radius:7px;padding:6px 11px;font:inherit;font-size:12px;
      cursor:pointer}
.copy:hover{color:#e8eaed;border-color:#25415f}
.hint{color:#5b606e;font-size:12px;margin:8px 0 0}
pre{background:#0d1016;border:1px solid #1c222b;border-radius:10px;padding:14px 16px;
    overflow-x:auto;margin:0;font-family:'IBM Plex Mono',ui-monospace,Menlo,monospace;
    font-size:12.5px;line-height:1.55;color:#cfd4dd}
pre[hidden]{display:none}
.links{display:flex;gap:10px;flex-wrap:wrap;margin-top:28px}
a.btn{display:inline-block;padding:9px 16px;border-radius:8px;text-decoration:none;
      font-size:13.5px;font-weight:600}
a.pri{background:#3b82f6;color:#fff}
a.sec{border:1px solid #1c222b;color:#b7bcc7}
.foot{margin-top:34px;color:#5b606e;font-size:12px;
      font-family:'IBM Plex Mono',ui-monospace,Menlo,monospace}
@media (max-width:420px){.wrap{padding:32px 16px 48px}.head img{width:44px;height:44px}}
"""


def snippets(url: str, t: dict) -> list[tuple[str, str]]:
    """Configs checked against each client's own documentation, not from memory.

    The first one carries no credential at all, and it comes first on purpose: a
    client that speaks MCP OAuth needs nothing to be created beforehand, and the
    old ordering silently taught that a key is always required.

    The rest DIFFER in shape, and not cosmetically. VS Code's root key is `servers`,
    not `mcpServers`, and `"type": "http"` is required: without it VS Code takes
    the URL for a command and tries to run it. Codex reads TOML and takes the
    key from an environment variable, sending it as `Authorization: Bearer` —
    our server accepts either header form, so it needs nothing special.

    A client that is not listed is served by the last paragraph: the address
    plus a header. Inventing a config shape to make the list look complete is
    worse than leaving that client unnamed.
    """
    return [
        # The credential-free form comes first: a client that speaks OAuth needs
        # nothing created beforehand, and the old tab order silently taught that
        # a key is always required.
        ("Без ключа · OAuth" if t is RU else "No key · OAuth",
         '{ "mcpServers": {\n'
         '    "teamtoken": {\n'
         '      "type": "http",\n'
         f'      "url": "{url}"\n'
         '    }\n'
         '} }'),
        ("Claude · Cursor · Cline",
         '{ "mcpServers": {\n'
         '    "teamtoken": {\n'
         '      "type": "http",\n'
         f'      "url": "{url}",\n'
         '      "headers": { "x-api-key": "sk-…" }\n'
         '    }\n'
         '} }'),
        ("VS Code",
         '// .vscode/mcp.json — the key here is "servers", not "mcpServers"\n'
         '{ "servers": {\n'
         '    "teamtoken": {\n'
         '      "type": "http",\n'
         f'      "url": "{url}",\n'
         '      "headers": { "x-api-key": "sk-…" }\n'
         '    }\n'
         '} }'),
        ("Codex CLI",
         '# ~/.codex/config.toml\n'
         '[mcp_servers.teamtoken]\n'
         f'url = "{url}"\n'
         'bearer_token_env_var = "TEAMTOKEN_API_KEY"'),
    ]


def page(url: str, icon: str, cabinet: str, docs: str, lang: str) -> bytes:
    t = RU if lang.lower().startswith("ru") else EN
    blocks = snippets(url, t)
    tabs = "".join(
        f'<button class="tab" role="tab" data-i="{i}" '
        f'aria-selected="{"true" if i == 0 else "false"}">{label}</button>'
        for i, (label, _) in enumerate(blocks))
    pres = "".join(
        f'<pre data-i="{i}"{"" if i == 0 else " hidden"}>{code}</pre>'
        for i, (_, code) in enumerate(blocks))
    html = f"""<!doctype html>
<html lang="{'ru' if t is RU else 'en'}">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>TeamToken MCP</title>
<style>{_CSS}</style>
<div class="wrap">
  <div class="head">
    <img src="{icon}" alt="TeamToken" width="52" height="52">
    <div>
      <h1>TeamToken MCP</h1>
      <div class="addr">{url}</div>
    </div>
  </div>
  <p>{t['lead']}</p>

  <h2>{t['way1_t']}</h2>
  <p>{t['way1']}</p>

  <h2>{t['way2_t']}</h2>
  <p>{t['way2']}</p>

  <h2>{t['cfg_t']}</h2>
  <div class="tabs" role="tablist">{tabs}
    <button class="copy" id="copy">{t['copy']}</button>
  </div>
  {pres}

  <h2>{t['tools_t']}</h2>
  <p>{t['tools']}</p>

  <div class="links">
    <a class="btn pri" href="{docs}">{t['docs']}</a>
    <a class="btn sec" href="{cabinet}">{t['keys']}</a>
  </div>
  <div class="foot">teamToken gateway</div>
</div>
<script>
(function () {{
  var tabs = document.querySelectorAll('.tab');
  var pres = document.querySelectorAll('pre[data-i]');
  var copy = document.getElementById('copy');
  var cur = 0;
  tabs.forEach(function (b) {{
    b.addEventListener('click', function () {{
      cur = +b.dataset.i;
      tabs.forEach(function (x) {{ x.setAttribute('aria-selected', x === b ? 'true' : 'false'); }});
      pres.forEach(function (p) {{ p.hidden = +p.dataset.i !== cur; }});
      copy.textContent = {t['copy']!r};
    }});
  }});
  copy.addEventListener('click', function () {{
    var text = pres[cur].textContent;
    // The fallback is not for ancient browsers but for embedded webviews:
    // navigator.clipboard is often missing there, and the button would then
    // silently do nothing.
    var done = function () {{
      copy.textContent = {t['copied']!r};
      setTimeout(function () {{ copy.textContent = {t['copy']!r}; }}, 1600);
    }};
    // A race against a timer, not just then(done, fallback): in some webviews
    // the promise neither resolves nor rejects, and the button then stays
    // silent forever. Measured in a headless browser: writeText exists, no
    // answer ever comes.
    var settled = false;
    var finish = function (fn) {{ if (!settled) {{ settled = true; fn(); }} }};
    if (navigator.clipboard && navigator.clipboard.writeText) {{
      navigator.clipboard.writeText(text).then(
        function () {{ finish(done); }}, function () {{ finish(fallback); }});
      setTimeout(function () {{ finish(fallback); }}, 1200);
    }} else {{ finish(fallback); }}
    function fallback() {{
      var a = document.createElement('textarea');
      a.value = text; a.style.position = 'fixed'; a.style.opacity = '0';
      document.body.appendChild(a); a.select();
      var ok = false;
      try {{ ok = document.execCommand('copy'); }} catch (e) {{}}
      document.body.removeChild(a);
      if (ok) {{ return done(); }}
      // Both paths can fail (a webview with no clipboard, a policy block). A
      // silent button is then worse than no button: the user believes the
      // config is on the clipboard and pastes whatever was there before. So
      // select the block and say what to press.
      var r = document.createRange();
      r.selectNodeContents(pres[cur]);
      var sel = window.getSelection();
      sel.removeAllRanges(); sel.addRange(r);
      copy.textContent = {t['selected']!r};
    }}
  }});
}})();
</script>
"""
    return html.encode("utf-8")


def wants_html(accept: str) -> bool:
    """A browser asks for text/html; an MCP client for json and an event stream.

    The event stream is checked first on purpose: a client that sends both must
    still get JSON, or its parser breaks on our markup.
    """
    a = accept.lower()
    if "text/event-stream" in a or "application/json" in a:
        return False
    return "text/html" in a

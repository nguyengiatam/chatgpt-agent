// Browser-side helpers, injected into the ChatGPT tab via Edge's
// `execute ... javascript` AppleScript command.
//
// This file only DEFINES things - it never runs anything on load, so the same
// source can be eval'd by node in tests and injected into the page verbatim.
// chatgpt-cli.py appends the actual call expression.

(function (globalThis) {
  "use strict";

  // Everything version-fragile lives here. When ChatGPT ships a redesign,
  // this is the only block that should need touching.
  var SELECTORS = {
    composer: '#prompt-textarea, form div[contenteditable="true"]',
    sendButton:
      'button[data-testid="send-button"], button[data-testid="composer-send-button"], button[aria-label*="Send"]',
    stopButton: 'button[data-testid="stop-button"], button[aria-label*="Stop"]',
    assistantMessage: '[data-message-author-role="assistant"]',
    markdownBody: ".markdown, .prose",
  };

  // --- DOM -> Markdown -----------------------------------------------------

  function classOf(node) {
    var raw = node.className;
    if (typeof raw !== "string") raw = node.getAttribute ? node.getAttribute("class") : "";
    return raw || "";
  }

  function findDescendant(node, tagName) {
    var kids = node.childNodes || [];
    for (var i = 0; i < kids.length; i++) {
      var child = kids[i];
      if (child.nodeType !== 1) continue;
      if (child.tagName === tagName) return child;
      var deeper = findDescendant(child, tagName);
      if (deeper) return deeper;
    }
    return null;
  }

  function collectElements(node, acc) {
    var kids = node.childNodes || [];
    for (var i = 0; i < kids.length; i++) {
      if (kids[i].nodeType !== 1) continue;
      acc.push(kids[i]);
      collectElements(kids[i], acc);
    }
    return acc;
  }

  function containsNode(ancestor, node) {
    if (!ancestor || !node || ancestor === node) return ancestor === node;
    var kids = ancestor.childNodes || [];
    for (var i = 0; i < kids.length; i++) {
      if (kids[i] === node || containsNode(kids[i], node)) return true;
    }
    return false;
  }

  // ChatGPT renders the language as a header label above the code, not as a
  // language-* class on <code>. Take the first element outside the code that
  // holds one bare word and no button - the Run/Copy captions live in buttons.
  function headerLanguage(pre, code) {
    var elements = collectElements(pre, []);
    for (var i = 0; i < elements.length; i++) {
      var node = elements[i];
      if (node.tagName === "BUTTON") continue;
      if (containsNode(node, code) || containsNode(code, node)) continue;
      if (findDescendant(node, "BUTTON")) continue;
      var text = (node.textContent || "").trim();
      if (!/^[\w+#.-]{1,20}$/.test(text)) continue;
      return text.toLowerCase();
    }
    return "";
  }

  function renderChildren(node) {
    var out = "";
    var kids = node.childNodes || [];
    for (var i = 0; i < kids.length; i++) out += render(kids[i]);
    return out;
  }

  function renderList(node, ordered) {
    var lines = [];
    var kids = node.childNodes || [];
    var n = 0;
    for (var i = 0; i < kids.length; i++) {
      var child = kids[i];
      if (child.nodeType !== 1 || child.tagName !== "LI") continue;
      n += 1;
      var marker = ordered ? n + ". " : "- ";
      lines.push(marker + renderChildren(child).trim());
    }
    return "\n\n" + lines.join("\n") + "\n\n";
  }

  function render(node) {
    if (!node) return "";
    if (node.nodeType === 3) return node.nodeValue || "";
    if (node.nodeType !== 1) return "";

    var tag = node.tagName;

    if (tag === "PRE") {
      // The code element skips ChatGPT's language label and "Copy code" button.
      var code = findDescendant(node, "CODE");
      var body = (code ? code.textContent : node.textContent) || "";
      var explicit = code ? /language-([\w+#.-]+)/.exec(classOf(code)) : null;
      var lang = explicit ? explicit[1] : headerLanguage(node, code);
      return "\n\n```" + lang + "\n" + body.replace(/\s+$/, "") + "\n```\n\n";
    }
    if (tag === "CODE") return "`" + (node.textContent || "") + "`";
    if (tag === "STRONG" || tag === "B") return "**" + renderChildren(node) + "**";
    if (tag === "EM" || tag === "I") return "*" + renderChildren(node) + "*";
    if (tag === "A") {
      var href = node.getAttribute ? node.getAttribute("href") : null;
      var label = renderChildren(node);
      return href ? "[" + label + "](" + href + ")" : label;
    }
    if (tag === "BR") return "\n";
    if (tag === "HR") return "\n\n---\n\n";
    if (/^H[1-6]$/.test(tag)) {
      var level = parseInt(tag.slice(1), 10);
      return "\n\n" + new Array(level + 1).join("#") + " " + renderChildren(node).trim() + "\n\n";
    }
    if (tag === "P" || tag === "DIV") return "\n\n" + renderChildren(node) + "\n\n";
    if (tag === "UL") return renderList(node, false);
    if (tag === "OL") return renderList(node, true);
    if (tag === "BLOCKQUOTE") {
      var inner = renderChildren(node).trim();
      if (!inner) return "";
      return "\n\n" + inner.split("\n").map(function (l) { return "> " + l; }).join("\n") + "\n\n";
    }
    return renderChildren(node);
  }

  function toMarkdown(root) {
    if (!root) return "";
    return render(root).replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
  }

  // --- page actions --------------------------------------------------------

  function normalise(text) {
    return (text || "").replace(/\s+/g, " ").trim();
  }

  // Given the conversation as [{role, text}], locate the assistant message
  // answering `promptText`. Anchoring on our own prompt - rather than taking
  // the newest assistant message - is what keeps a shared tab from handing us
  // somebody else's reply. `isLast` tells the caller whether a global
  // "stop streaming" button can still be about us.
  //
  // The match is containment, not equality, because a sent prompt and its
  // rendered node are never the same string: ChatGPT renders the message as
  // markdown, so textContent loses backticks and other syntax, and a long
  // message grows "See more"/"Collapse" button captions at the end. Callers
  // therefore pass a short marker they embedded in the prompt, not the prompt.
  function pickReply(entries, promptText) {
    var wanted = normalise(promptText);
    if (!wanted) return null;
    var anchor = -1;
    for (var i = 0; i < entries.length; i++) {
      if (entries[i].role === "user" && normalise(entries[i].text).indexOf(wanted) !== -1) anchor = i;
    }
    if (anchor === -1) return null;

    for (var j = anchor + 1; j < entries.length; j++) {
      if (entries[j].role !== "assistant") continue;
      var isLast = true;
      for (var k = j + 1; k < entries.length; k++) {
        if (entries[k].role === "assistant") { isLast = false; break; }
      }
      return { index: j, isLast: isLast };
    }
    return null;
  }

  function composer() {
    return document.querySelector(SELECTORS.composer);
  }

  function lastAssistantBody() {
    var msgs = document.querySelectorAll(SELECTORS.assistantMessage);
    if (!msgs.length) return null;
    var last = msgs[msgs.length - 1];
    return last.querySelector(SELECTORS.markdownBody) || last;
  }

  // Reports whether the page is usable before we try to type into it.
  function probe() {
    return {
      ok: !!composer(),
      url: location.href,
      error: composer() ? null : "composer-not-found",
      count: document.querySelectorAll(SELECTORS.assistantMessage).length,
    };
  }

  // The composer is a ProseMirror contenteditable, not a textarea: assigning
  // .value does nothing. execCommand('insertText') emits the real
  // beforeinput/input sequence ProseMirror listens for; a synthetic paste is
  // the fallback if the browser ever drops execCommand.
  function insert(text) {
    var box = composer();
    if (!box) return { ok: false, error: "composer-not-found" };
    box.focus();

    var sel = window.getSelection();
    var range = document.createRange();
    range.selectNodeContents(box);
    sel.removeAllRanges();
    sel.addRange(range);

    var wrote = false;
    try {
      wrote = document.execCommand("insertText", false, text);
    } catch (e) {
      wrote = false;
    }

    if (!wrote || !box.textContent) {
      try {
        var dt = new DataTransfer();
        dt.setData("text/plain", text);
        box.dispatchEvent(
          new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true })
        );
      } catch (e) {
        return { ok: false, error: "insert-failed: " + e.message };
      }
    }
    return { ok: !!box.textContent, error: box.textContent ? null : "composer-still-empty" };
  }

  function submit() {
    var btn = document.querySelector(SELECTORS.sendButton);
    if (!btn) return { ok: false, error: "send-button-not-found" };
    if (btn.disabled) return { ok: false, error: "send-button-disabled" };
    btn.click();
    return { ok: true, error: null };
  }

  // One poll sample: everything the Python side needs to decide "is it done?".
  function state(promptText) {
    var nodes = document.querySelectorAll("[data-message-author-role]");
    var entries = Array.prototype.map.call(nodes, function (n) {
      return { role: n.getAttribute("data-message-author-role"), text: n.textContent || "" };
    });
    var hit = pickReply(entries, promptText);
    var body = null;
    if (hit) {
      var node = nodes[hit.index];
      body = node.querySelector(SELECTORS.markdownBody) || node;
    }
    return {
      ok: true,
      found: !!hit,
      isLast: hit ? hit.isLast : false,
      streaming: !!document.querySelector(SELECTORS.stopButton),
      text: body ? body.textContent || "" : "",
      markdown: body ? toMarkdown(body) : "",
    };
  }

  globalThis.CGPT = {
    SELECTORS: SELECTORS,
    toMarkdown: toMarkdown,
    pickReply: pickReply,
    probe: probe,
    insert: insert,
    submit: submit,
    state: state,
  };
})(typeof globalThis !== "undefined" ? globalThis : this);

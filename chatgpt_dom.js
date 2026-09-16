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
    fileInput: 'input#upload-files, input[type="file"]:not([accept*="image"])',
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

    // Buttons inside an assistant message are always chrome, never prose: the
    // Copy and Run captions on a code block, and the file-citation chips that
    // appear whenever the turn carried an attachment. Left in, a citation like
    // "c2c-8fd3427b +1" lands in the middle of a sentence in the answer.
    if (tag === "BUTTON") return "";

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

    // execCommand('insertText') emits the real beforeinput/input sequence that
    // ProseMirror listens for; assigning .value to a contenteditable does
    // nothing. There is no fallback here on purpose: a synthetic ClipboardEvent
    // was measured returning in 0.1s while leaving the composer empty, because
    // ProseMirror ignores an untrusted paste. Keeping it would have looked like
    // a safety net and caught nothing.
    var wrote = false;
    try {
      wrote = document.execCommand("insertText", false, text);
    } catch (e) {
      return { ok: false, error: "insert-failed: " + e.message };
    }
    if (!wrote && text) return { ok: false, error: "insert-rejected" };
    return { ok: !text || !!box.textContent, error: null };
  }

  // Attaching sidesteps the composer entirely, and that is the whole point.
  // execCommand("insertText") builds one ProseMirror block node per newline in
  // a single transaction, so the cost is quadratic in LINES: the same 40k
  // characters took 0.1s on one line and 116s across two thousand. A file
  // reaches the same content into the conversation in 0.1s regardless.
  function attach(content, name) {
    var input = document.querySelector(SELECTORS.fileInput);
    if (!input) return { ok: false, error: "file-input-not-found" };
    try {
      var transfer = new DataTransfer();
      transfer.items.add(new File([content], name, { type: "text/plain" }));
      input.files = transfer.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
      return { ok: input.files.length === 1, bytes: (content || "").length, error: null };
    } catch (e) {
      return { ok: false, error: "attach-failed: " + e.message };
    }
  }

  // ChatGPT blocks the send button with aria-disabled, not with the DOM
  // `disabled` property - that one stays false throughout. Measured while a
  // file uploaded: disabled=false the entire time, aria-disabled "false" ->
  // "true" -> "false" as the upload finished. Reading only `disabled` meant
  // clicking a button React considered dead, reporting success, and sending
  // nothing.
  function sendBlocked(button) {
    if (!button) return true;
    if (button.disabled) return true;
    return button.getAttribute("aria-disabled") === "true";
  }

  function submit() {
    var btn = document.querySelector(SELECTORS.sendButton);
    if (!btn) return { ok: false, error: "send-button-not-found" };
    if (sendBlocked(btn)) return { ok: false, error: "send-button-disabled" };
    btn.click();
    return { ok: true, error: null };
  }

  // Readiness must be a POSITIVE signal. aria-disabled clearing is not one:
  // it goes false both when the upload finished and when ChatGPT gave up and
  // removed the attachment, and those look identical from outside. Observed
  // live - a chip present at 1s and 3s, gone by 6s, with the send button
  // unblocked either way. Gating on that sent a turn with no file in it.
  //
  // So require the chip bearing our filename to still be there AND the button
  // to be free. Missing chip means not ready, never ready-enough, so a broken
  // selector surfaces as a timeout rather than as a message sent short.
  function attachmentReady(name) {
    var btn = document.querySelector(SELECTORS.sendButton);
    if (sendBlocked(btn)) return { ok: true, ready: false, reason: "send-blocked" };
    if (!name) return { ok: true, ready: true, reason: null };
    return { ok: true, ready: hasChip(name), reason: hasChip(name) ? null : "chip-gone" };
  }

  // Structural only, for both guards. There used to be an innerText fallback
  // here and a plain textContent scan below, and between them the filename our
  // own note carried satisfied everything: the composer held the note, and so
  // did the sent turn. A name in prose is not an attachment. A labelled
  // control is.
  function labelledWith(root, name) {
    if (!root || !name) return false;
    var nodes = root.querySelectorAll("[aria-label], [title]");
    for (var i = 0; i < nodes.length; i++) {
      var label = nodes[i].getAttribute("aria-label") || nodes[i].getAttribute("title") || "";
      if (label.indexOf(name) !== -1) return true;
    }
    return false;
  }

  function hasChip(name) {
    return labelledWith(document.querySelector("form") || document.body, name);
  }

  // After sending, the file should have moved into our own turn as a tile -
  // observed live as DIV/BUTTON carrying aria-label="<name>". Reading the
  // structure rather than the text keeps this independent of what ChatGPT
  // says, what language it says it in, and what we ourselves typed.
  function sentWithAttachment(name) {
    var users = document.querySelectorAll('[data-message-author-role="user"]');
    if (!users.length) return { ok: true, sent: false, carried: false };
    return { ok: true, sent: true, carried: labelledWith(users[users.length - 1], name) };
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
    attach: attach,
    attachmentReady: attachmentReady,
    hasChip: hasChip,
    labelledWith: labelledWith,
    sentWithAttachment: sentWithAttachment,
    sendBlocked: sendBlocked,
    submit: submit,
    state: state,
  };
})(typeof globalThis !== "undefined" ? globalThis : this);

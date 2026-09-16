// Tests for the browser-side helpers in chatgpt_dom.js.
// Runs in plain node against hand-built node objects - no jsdom, no browser.
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const { test } = require("node:test");

const sandbox = {};
new Function("globalThis", fs.readFileSync(path.join(__dirname, "chatgpt_dom.js"), "utf8"))(sandbox);
const CGPT = sandbox.CGPT;

// --- minimal DOM stand-ins -------------------------------------------------
function txt(value) {
  return { nodeType: 3, nodeValue: value, textContent: value, childNodes: [] };
}
function el(tagName, children = [], attrs = {}) {
  const kids = children.map((c) => (typeof c === "string" ? txt(c) : c));
  return {
    nodeType: 1,
    tagName: tagName.toUpperCase(),
    childNodes: kids,
    className: attrs.class || "",
    getAttribute: (name) => (name in attrs ? attrs[name] : null),
    get textContent() {
      return kids.map((k) => k.textContent).join("");
    },
  };
}

const md = (node) => CGPT.toMarkdown(node);

test("paragraph becomes plain text", () => {
  assert.strictEqual(md(el("div", [el("p", ["Hello there"])])), "Hello there");
});

test("two paragraphs are separated by a blank line", () => {
  const node = el("div", [el("p", ["One"]), el("p", ["Two"])]);
  assert.strictEqual(md(node), "One\n\nTwo");
});

test("fenced code block keeps its language", () => {
  const node = el("div", [
    el("pre", [el("code", ["print('hi')\n"], { class: "language-python" })]),
  ]);
  assert.strictEqual(md(node), "```python\nprint('hi')\n```");
});

test("code block without a language still gets fences", () => {
  const node = el("div", [el("pre", [el("code", ["make build\n"])])]);
  assert.strictEqual(md(node), "```\nmake build\n```");
});

test("inline code gets backticks", () => {
  const node = el("div", [el("p", ["run ", el("code", ["npm test"]), " now"])]);
  assert.strictEqual(md(node), "run `npm test` now");
});

test("bold and italic map to markdown emphasis", () => {
  const node = el("div", [el("p", [el("strong", ["bold"]), " and ", el("em", ["it"])])]);
  assert.strictEqual(md(node), "**bold** and *it*");
});

test("headings use the matching number of hashes", () => {
  assert.strictEqual(md(el("div", [el("h3", ["Title"])])), "### Title");
});

test("unordered list items use dashes", () => {
  const node = el("div", [el("ul", [el("li", ["one"]), el("li", ["two"])])]);
  assert.strictEqual(md(node), "- one\n- two");
});

test("ordered list items are numbered in sequence", () => {
  const node = el("div", [el("ol", [el("li", ["first"]), el("li", ["second"])])]);
  assert.strictEqual(md(node), "1. first\n2. second");
});

test("links keep their href", () => {
  const node = el("div", [el("p", [el("a", ["docs"], { href: "https://x.com/" })])]);
  assert.strictEqual(md(node), "[docs](https://x.com/)");
});

test("br becomes a newline", () => {
  const node = el("div", [el("p", ["a", el("br"), "b"])]);
  assert.strictEqual(md(node), "a\nb");
});

test("blockquote is prefixed on every line", () => {
  const node = el("div", [el("blockquote", [el("p", ["quoted"])])]);
  assert.strictEqual(md(node), "> quoted");
});

test("a code block inside a list item is not flattened", () => {
  const node = el("div", [
    el("ul", [el("li", [el("pre", [el("code", ["ls -la"], { class: "language-bash" })])])]),
  ]);
  assert.ok(md(node).includes("```bash"), md(node));
});

test("leading and trailing whitespace is trimmed", () => {
  assert.strictEqual(md(el("div", ["  ", el("p", ["body"]), "  "])), "body");
});

test("empty element yields an empty string", () => {
  assert.strictEqual(md(el("div", [])), "");
});

// --- language detection against ChatGPT's real code-block markup ------------
// Live DOM (checked 2026-09-16): <code> carries NO language-* class. The
// language sits in a header div next to the Run/Copy buttons.

test("language comes from the header label when code has no class", () => {
  const node = el("div", [
    el("pre", [
      el("div", [el("div", ["Python"]), el("div", [el("button", ["Ch\u1ea1y"])])]),
      el("div", [el("code", ["print(1)"])]),
    ]),
  ]);
  assert.strictEqual(md(node), "```python\nprint(1)\n```");
});

test("header label is ignored when it is a button caption", () => {
  const node = el("div", [
    el("pre", [
      el("div", [el("button", ["Sao ch\u00e9p"])]),
      el("div", [el("code", ["ls"])]),
    ]),
  ]);
  assert.strictEqual(md(node), "```\nls\n```");
});

test("multi-word header labels are not treated as a language", () => {
  const node = el("div", [
    el("pre", [el("div", [el("div", ["Plain text"])]), el("div", [el("code", ["hi"])])]),
  ]);
  assert.strictEqual(md(node), "```\nhi\n```");
});

test("an explicit language-* class still wins over the header", () => {
  const node = el("div", [
    el("pre", [
      el("div", [el("div", ["Python"])]),
      el("div", [el("code", ["x=1"], { class: "language-ruby" })]),
    ]),
  ]);
  assert.strictEqual(md(node), "```ruby\nx=1\n```");
});

test("header label never leaks into the code body", () => {
  const node = el("div", [
    el("pre", [
      el("div", [el("div", ["Python"]), el("div", [el("button", ["Ch\u1ea1y"])])]),
      el("div", [el("code", ["print(1)"])]),
    ]),
  ]);
  assert.ok(!md(node).includes("Ch\u1ea1y"), md(node));
});

// --- picking OUR reply out of a shared conversation ------------------------
// The tab can be driven by a human or a second CLI run at the same time, so
// "the last assistant message" is not necessarily an answer to our prompt.

const U = (text) => ({ role: "user", text });
const A = (text) => ({ role: "assistant", text });
const pick = (entries, prompt) => CGPT.pickReply(entries, prompt);

test("picks the assistant message following our prompt", () => {
  assert.deepStrictEqual(pick([U("ping"), A("pong")], "ping"), { index: 1, isLast: true });
});

test("ignores a later message from someone else sharing the tab", () => {
  const convo = [U("ping"), A("pong"), U("their question"), A("their answer")];
  assert.deepStrictEqual(pick(convo, "ping"), { index: 1, isLast: false });
});

test("ignores an earlier exchange from someone else", () => {
  const convo = [U("their question"), A("their answer"), U("ping"), A("pong")];
  assert.deepStrictEqual(pick(convo, "ping"), { index: 3, isLast: true });
});

test("returns null while our prompt has no reply yet", () => {
  assert.strictEqual(pick([U("ping")], "ping"), null);
});

test("returns null when our prompt is not in the conversation", () => {
  assert.strictEqual(pick([U("other"), A("answer")], "ping"), null);
});

test("asking the same thing twice picks the most recent reply", () => {
  const convo = [U("ping"), A("old answer"), U("ping"), A("new answer")];
  assert.deepStrictEqual(pick(convo, "ping"), { index: 3, isLast: true });
});

test("matches despite whitespace being collapsed on render", () => {
  assert.deepStrictEqual(pick([U("line one line two"), A("ok")], "line one\n\nline two"), {
    index: 1,
    isLast: true,
  });
});

test("a foreign prompt landing between ours and its reply is skipped", () => {
  const convo = [U("ping"), U("their question"), A("pong")];
  assert.deepStrictEqual(pick(convo, "ping"), { index: 2, isLast: true });
});

test("a marker embedded in a longer prompt still anchors", () => {
  const convo = [U("[c2c:abc12345]\n\nplease review"), A("here you go")];
  assert.deepStrictEqual(pick(convo, "[c2c:abc12345]"), { index: 1, isLast: true });
});

test("markdown stripped from the rendered message does not break the anchor", () => {
  // The DOM never shows the backticks we sent, so only the marker can match.
  const convo = [U("[c2c:abc12345] use c2c blocks"), A("ok")];
  assert.deepStrictEqual(pick(convo, "[c2c:abc12345]"), { index: 1, isLast: true });
});

test("See more captions appended by ChatGPT do not break the anchor", () => {
  const convo = [U("[c2c:abc12345] long promptSee moreCollapse"), A("ok")];
  assert.deepStrictEqual(pick(convo, "[c2c:abc12345]"), { index: 1, isLast: true });
});

test("a different marker does not match", () => {
  const convo = [U("[c2c:aaaaaaaa] mine"), A("mine")];
  assert.strictEqual(pick(convo, "[c2c:bbbbbbbb]"), null);
});

test("an empty anchor matches nothing rather than everything", () => {
  assert.strictEqual(pick([U("anything"), A("reply")], ""), null);
});

test("a missing send button counts as blocked", () => {
  assert.strictEqual(CGPT.sendBlocked(null), true);
});

test("the DOM disabled property blocks", () => {
  assert.strictEqual(CGPT.sendBlocked({ disabled: true, getAttribute: () => null }), true);
});

test("aria-disabled blocks even while the DOM property says otherwise", () => {
  // This is the real case: ChatGPT leaves .disabled false during an upload and
  // marks the button aria-disabled="true". Reading only .disabled clicked a
  // dead button and reported success.
  assert.strictEqual(CGPT.sendBlocked({ disabled: false, getAttribute: () => "true" }), true);
});

test("aria-disabled false does not block", () => {
  assert.strictEqual(CGPT.sendBlocked({ disabled: false, getAttribute: () => "false" }), false);
});

test("a button with no aria-disabled attribute does not block", () => {
  assert.strictEqual(CGPT.sendBlocked({ disabled: false, getAttribute: () => null }), false);
});

test("a file-citation chip is not rendered into the answer", () => {
  // Shape observed live: BUTTON > SPAN > P holding the attachment's name.
  const body = el("div", [
    el("p", ["The fence blocks traversal."]),
    el("button", [el("p", ["c2c-8fd3427b +1"])]),
    el("p", ["It is not a sandbox."]),
  ]);
  const out = md(body);
  assert.ok(!out.includes("c2c-8fd3427b"), out);
  assert.ok(out.includes("The fence blocks traversal."));
  assert.ok(out.includes("It is not a sandbox."));
});

test("a copy caption on a code block is not rendered either", () => {
  const out = md(el("div", [el("button", ["Sao chép"]), el("p", ["real text"])]));
  assert.ok(!out.includes("Sao chép"), out);
  assert.ok(out.includes("real text"));
});

// --- attachment readiness --------------------------------------------------
// The gate must be a positive signal. Observed live: a chip present at 1s and
// 3s was gone by 6s while the send button read unblocked either way, so
// "button is free" alone sent a turn carrying no file.

function withForm(labels, innerText, sendAttrs) {
  const form = {
    innerText: innerText || "",
    querySelectorAll: () => (labels || []).map((v) => ({ getAttribute: () => v })),
  };
  global.document = {
    querySelector: (sel) => (sel === "form" ? form : { disabled: false, getAttribute: () => (sendAttrs || "false") }),
    querySelectorAll: () => [],
    body: form,
  };
  return form;
}

test("ready when the chip is present and the button is free", () => {
  withForm(["c2c-abc.txt"], "", "false");
  assert.strictEqual(CGPT.attachmentReady("c2c-abc.txt").ready, true);
});

test("not ready while the send button is blocked", () => {
  withForm(["c2c-abc.txt"], "", "true");
  assert.strictEqual(CGPT.attachmentReady("c2c-abc.txt").ready, false);
});

test("not ready once the chip has gone, even with the button free", () => {
  // This is the real failure: the upload was dropped and the button unblocked.
  withForm([], "", "false");
  const state = CGPT.attachmentReady("c2c-abc.txt");
  assert.strictEqual(state.ready, false);
  assert.strictEqual(state.reason, "chip-gone");
});

test("a name appearing only in the form's text is not a chip", () => {
  // This asserted the opposite in 0.2.2, which is how the hole got in: the
  // innerText fallback it protected was what let our own note pass for an
  // attachment. A chip is a labelled control or it is nothing.
  withForm([], "Cao | c2c-abc.txt | Tai lieu", "false");
  assert.strictEqual(CGPT.attachmentReady("c2c-abc.txt").ready, false);
});

test("a chip carrying the name in title instead of aria-label counts", () => {
  global.document = {
    querySelector: (sel) => (sel === "form"
      ? { querySelectorAll: () => [{ getAttribute: (a) => (a === "title" ? "c2c-abc.txt" : null) }] }
      : { disabled: false, getAttribute: () => "false" }),
    querySelectorAll: () => [],
    body: {},
  };
  assert.strictEqual(CGPT.attachmentReady("c2c-abc.txt").ready, true);
});

test("with no filename the button alone decides", () => {
  withForm([], "", "false");
  assert.strictEqual(CGPT.attachmentReady("").ready, true);
});

// --- the checks must be able to FAIL ---------------------------------------
// Three fixes in a row were satisfied by evidence the tool wrote itself. These
// are the tests that would have caught the second and third: they assert the
// negative case, which is the only case a guard exists for.

test("the plugin's own note does not satisfy the chip check", () => {
  // Regression: the typed note used to carry the filename, so form.innerText
  // always contained it and the fail-closed gate could never close.
  withForm([], "Everything for this turn is in the file attached to this message.", "false");
  assert.strictEqual(CGPT.attachmentReady("c2c-abc.txt").ready, false);
});

test("a chip whose name only appears in prose is not a chip", () => {
  withForm([], "I will read c2c-abc.txt shortly", "false");
  assert.strictEqual(CGPT.attachmentReady("c2c-abc.txt").ready, false,
    "prose mentioning the name must not pass for an attachment");
});

test("sentWithAttachment fails when the turn is only our note", () => {
  const note = "Everything for this turn is in the file attached to this message.";
  global.document = {
    querySelector: () => null,
    querySelectorAll: () => [{ textContent: note }],
    body: { innerText: "" },
  };
  assert.strictEqual(CGPT.sentWithAttachment("c2c-abc.txt").carried, false);
});

test("sentWithAttachment passes when the turn really carries the file", () => {
  global.document = {
    querySelector: () => null,
    querySelectorAll: () => [{ textContent: "note here c2c-abc.txt" }],
    body: { innerText: "" },
  };
  assert.strictEqual(CGPT.sentWithAttachment("c2c-abc.txt").carried, true);
});

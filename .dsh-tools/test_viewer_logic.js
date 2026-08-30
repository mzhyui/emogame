// Sanity test: run the viewer's highlight+render pipeline on real app.py.
// Invariants: every rendered row is span-balanced, global balance holds,
// cross-line triple-quoted strings keep tok-str coloring across rows.
const fs = require('fs');

const code = fs.readFileSync('/home/mzhyui/git/emogame/app.py', 'utf8');

function esc(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

const TOK = /(#.*$)|((?:[fFrRbBuU]{0,2})(?:"""[\s\S]*?"""|'''[\s\S]*?'''))|((?:[fFrRbBuU]{0,2})(?:"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'))|(@[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)|(\b\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?\b)|(\b(?:def|class|return|import|from|if|elif|else|for|while|in|not|and|or|is|None|True|False|with|as|try|except|finally|raise|pass|break|continue|lambda|global|nonlocal|yield|assert|del|async|await|match|case)\b)|(\b[A-Za-z_]\w*(?=\())|(\b[A-Za-z_]\w*\b)/gm;

const CLS = ['tok-com', 'tok-str', 'tok-str', 'tok-dec', 'tok-num', 'tok-kw', 'tok-fn', null];
const BUILTINS = new Set(['print','len','dict','list','set','tuple','str','int','float','bool','range','enumerate','zip','map','filter','sum','min','max','sorted','reversed','any','all','abs','round','isinstance','type','getattr','setattr','hasattr','repr','open','super','staticmethod','classmethod','property','Exception','ValueError','KeyError','TypeError','NotImplementedError','object','self']);

function highlight(src) {
  let out = '', last = 0, m;
  TOK.lastIndex = 0;
  while ((m = TOK.exec(src)) !== null) {
    out += esc(src.slice(last, m.index));
    let cls = null;
    for (let i = 1; i < 9; i++) {
      if (m[i] !== undefined) { cls = CLS[i - 1]; break; }
    }
    let t = m[0];
    if (cls === null && BUILTINS.has(t)) cls = 'tok-builtin';
    out += cls ? '<span class="' + cls + '">' + esc(t) + '</span>' : esc(t);
    last = m.index + t.length;
    if (TOK.lastIndex === m.index) TOK.lastIndex++;
  }
  out += esc(src.slice(last));
  return out;
}

function render(src) {
  const html = highlight(src);
  const frags = html.split('\n');
  if (frags.length && frags[frags.length - 1] === '') frags.pop();
  const out = [];
  const stack = [];
  const re = /<span class="([^"]+)">|<\/span>/g;
  for (let i = 0; i < frags.length; i++) {
    const body = frags[i];
    const before = stack.slice();
    let m;
    re.lastIndex = 0;
    while ((m = re.exec(body)) !== null) {
      if (m[1] !== undefined) stack.push(m[1]);
      else if (stack.length > 0) stack.pop();
    }
    const prefix = before.map((c) => '<span class="' + c + '">').join('');
    const suffix = '</span>'.repeat(stack.length);
    out.push(
      '<div class="code-line"><span class="code-ln">' + (i + 1) +
      '</span><span class="code-txt">' + prefix + body + suffix + '</span></div>'
    );
  }
  return out.join('\n');
}

const rendered = render(code);
const rows = rendered.split('\n');
const srcLines = code.split('\n');
if (srcLines[srcLines.length - 1] === '') srcLines.pop();

let fail = false;

// 1) line count
console.log('source lines:', srcLines.length, '| rendered rows:', rows.length);
if (rows.length !== srcLines.length) { console.error('LINE COUNT MISMATCH'); fail = true; }

// 2) every row balanced (content spans only — strip the two wrapper spans)
let bad = 0;
rows.forEach((ln, i) => {
  const inner = ln.replace(/^<div class="code-line"><span class="code-ln">\d+<\/span><span class="code-txt">/, '')
                  .replace(/<\/span><\/div>$/, '');
  const opens = (inner.match(/<span class="/g) || []).length;
  const closes = (inner.match(/<\/span>/g) || []).length;
  if (opens !== closes) { bad++; console.error('unbalanced row', i + 1, 'opens', opens, 'closes', closes); fail = true; }
});
console.log('unbalanced rows:', bad);

// 3) cross-line string regions keep tok-str across rows
const regions = [
  { start: 0, end: 8 },    // module docstring rows 1-9
  { start: 53, end: 76 },  // st.markdown CSS rows 54-77
  { start: 150, end: 153 },// query triple-string rows 151-154
];
for (const { start, end } of regions) {
  let allStr = true;
  for (let i = start; i <= end; i++) {
    if (!rows[i].includes('tok-str')) { allStr = false; break; }
  }
  console.log((allStr ? 'PASS' : 'FAIL') + `  rows ${start + 1}-${end + 1} all tok-str`);
  if (!allStr) fail = true;
}

// 4) sample token checks
const checks = [
  ['keyword def', /<span class="tok-kw">def<\/span>/],
  ['comment', /<span class="tok-com">#/],
  ['decorator', /<span class="tok-dec">@st\.cache_data<\/span>/],
  ["string", /<span class="tok-str">"""/],
  ["fn call", /<span class="tok-fn">set_page_config<\/span>/],
  ['builtin', /<span class="tok-builtin">dict<\/span>/],
];
for (const [name, re] of checks) {
  const pass = re.test(rendered);
  if (!pass) fail = true;
  console.log((pass ? 'PASS' : 'FAIL') + '  ' + name);
}

process.exit(fail ? 1 : 0);

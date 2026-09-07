const WebText = (() => {
  function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // The three result panels carry a study's caveats, and two formatting
  // markers inside those strings are load-bearing: <b> is the emphasis on a
  // warning, and <span id/class="...-warn"> is what makes a warning amber
  // (#f59e0b). The first safe version of this renderer stripped every tag
  // before assigning textContent — safe, but it silently deleted both
  // signals, and ten warning lines lost their only visual marking along with
  // the risk of markup. Honour exactly those two markers as real elements and
  // put every other character in as text, so no server or artifact value can
  // become markup.
  const INLINE_MARKUP =
    /(<b>[\s\S]*?<\/b>|<span (?:id|class)="[A-Za-z0-9 _-]+">[\s\S]*?<\/span>)/;

  //: The legacy strings repeat ONE id across many lines, which is invalid
  //: HTML. Carry the styling across as a class instead.
  const LEGACY_ID_CLASS = { 'suggest-results-warn': 'suggest-warn' };

  function markupNode(part, doc) {
    const bold = /^<b>([\s\S]*)<\/b>$/.exec(part);
    if (bold) {
      const node = doc.createElement('strong');
      node.textContent = bold[1];
      return node;
    }
    const span =
      /^<span (?:id|class)="([A-Za-z0-9 _-]+)">([\s\S]*)<\/span>$/.exec(part);
    if (span) {
      const node = doc.createElement('span');
      node.className = LEGACY_ID_CLASS[span[1]] ?? span[1];
      node.textContent = span[2];
      return node;
    }
    return null;
  }

  function renderTextLines(container, lines, doc) {
    const documentRef = doc ?? globalThis.document;
    container.replaceChildren();
    lines.forEach((line, index) => {
      if (index) container.appendChild(documentRef.createElement('br'));
      for (const part of String(line).split(INLINE_MARKUP)) {
        if (!part) continue;
        const marked = markupNode(part, documentRef);
        if (marked) {
          container.appendChild(marked);
          continue;
        }
        // Anything else — including a stray '<' from a server message —
        // becomes text, never markup.
        const span = documentRef.createElement('span');
        span.textContent = part;
        container.appendChild(span);
      }
    });
  }

  return { escapeHtml, renderTextLines };
})();

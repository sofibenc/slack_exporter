// Recherche plein texte dans l'archive, entièrement locale.
// L'index (window.SLACK_SEARCH) est chargé par assets/search-index.js.
"use strict";
(function () {
  function normalize(text) {
    return text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  }

  // Mots séparés par des espaces ; "une expression" entre guillemets reste d'un seul bloc.
  function parseQuery(query) {
    const terms = [];
    const pattern = /"([^"]+)"|(\S+)/g;
    let match;
    while ((match = pattern.exec(query)) !== null) {
      const term = normalize((match[1] || match[2]).trim());
      if (term) terms.push(term);
    }
    return terms;
  }

  function dayStart(date) {
    return new Date(date + "T00:00:00").getTime() / 1000;
  }

  function search(index, criteria) {
    const terms = parseQuery(criteria.query || "");
    const author = normalize((criteria.author || "").trim());
    if (!terms.length && !author) return { results: [], terms };
    const from = criteria.from ? dayStart(criteria.from) : null;
    const to = criteria.to ? dayStart(criteria.to) + 86400 : null;
    if (!index._normalized) index._normalized = index.messages.map((m) => normalize(m[3]));

    const results = [];
    index.messages.forEach((m, i) => {
      const conversation = index.conversations[m[0]];
      if (criteria.type && conversation.type !== criteria.type) return;
      if (from !== null && m[2] < from) return;
      if (to !== null && m[2] >= to) return;
      if (author && !normalize(m[1]).includes(author)) return;
      const haystack = index._normalized[i];
      if (!terms.every((term) => haystack.includes(term))) return;
      results.push({
        conversation, author: m[1], ts: m[2], text: m[3], href: m[4], inThread: m[5] === 1,
      });
    });
    results.sort((a, b) => b.ts - a.ts);
    return { results, terms };
  }

  // Zones [début, fin[ à surligner dans le texte original. La normalisation garde la
  // longueur des textes usuels ; sinon, on renonce simplement au surlignage.
  function highlightRanges(text, terms) {
    const normalized = normalize(text);
    if (normalized.length !== text.length) return [];
    const ranges = [];
    for (const term of terms) {
      let at = normalized.indexOf(term);
      while (at !== -1) {
        ranges.push([at, at + term.length]);
        at = normalized.indexOf(term, at + term.length);
      }
    }
    ranges.sort((a, b) => a[0] - b[0]);
    const merged = [];
    for (const range of ranges) {
      const last = merged[merged.length - 1];
      if (last && range[0] <= last[1]) last[1] = Math.max(last[1], range[1]);
      else merged.push(range.slice());
    }
    return merged;
  }

  if (typeof module !== "undefined") {
    module.exports = { normalize, parseQuery, search, highlightRanges };
  }
  if (typeof document === "undefined") return;

  // ---- Interface (navigateur uniquement) ----
  const PAGE_SIZE = 100;
  const SNIPPET = 160;
  const $ = (id) => document.getElementById(id);
  const index = window.SLACK_SEARCH;
  let current = [];
  let shown = 0;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // Extrait autour de la première occurrence, construit sans innerHTML.
  function snippet(text, terms) {
    const ranges = highlightRanges(text, terms);
    let start = 0;
    if (ranges.length && text.length > SNIPPET) start = Math.max(0, ranges[0][0] - 60);
    const end = Math.min(text.length, start + SNIPPET);
    const out = el("p", "snippet");
    if (start > 0) out.append("…");
    let at = start;
    for (const [from, to] of ranges) {
      if (to <= start || from >= end) continue;
      const a = Math.max(from, start), b = Math.min(to, end);
      out.append(text.slice(at, a));
      out.append(el("mark", null, text.slice(a, b)));
      at = b;
    }
    out.append(text.slice(at, end));
    if (end < text.length) out.append("…");
    return out;
  }

  function renderMore(terms) {
    const list = $("results");
    for (const r of current.slice(shown, shown + PAGE_SIZE)) {
      const item = el("li", "result");
      const head = el("div", "head");
      const link = el("a", null, r.conversation.title);
      link.href = r.href;
      head.append(link, " · ", el("span", "author", r.author), " · ",
        el("time", null, new Date(r.ts * 1000).toLocaleString("fr-FR", { dateStyle: "medium", timeStyle: "short" })));
      if (r.inThread) head.append(" ", el("span", "meta", "(dans un fil)"));
      item.append(head, snippet(r.text, terms));
      list.append(item);
    }
    shown = Math.min(current.length, shown + PAGE_SIZE);
    $("more").hidden = shown >= current.length;
  }

  function run(event) {
    if (event) event.preventDefault();
    const { results, terms } = search(index, {
      query: $("q").value, author: $("author").value, type: $("type").value,
      from: $("from").value, to: $("to").value,
    });
    current = results;
    shown = 0;
    $("results").replaceChildren();
    const asked = $("q").value.trim() || $("author").value.trim();
    $("count").textContent = !asked ? "" :
      results.length === 0 ? "Aucun résultat." :
      results.length + (results.length > 1 ? " résultats" : " résultat");
    $("results").dataset.terms = JSON.stringify(terms);
    renderMore(terms);
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (!index) {
      $("count").textContent = "Index de recherche introuvable (assets/search-index.js).";
      return;
    }
    const authors = [...new Set(index.messages.map((m) => m[1]))].sort((a, b) => a.localeCompare(b, "fr"));
    for (const name of authors) {
      const option = document.createElement("option");
      option.value = name;
      $("authors").append(option);
    }
    $("form").addEventListener("submit", run);
    $("more").addEventListener("click", () => renderMore(JSON.parse($("results").dataset.terms || "[]")));
    $("status").textContent = index.messages.length.toLocaleString("fr-FR") + " messages indexés.";
    $("q").focus();
  });
})();

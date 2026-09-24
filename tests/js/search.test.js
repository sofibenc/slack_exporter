// Tests de la logique de recherche (src/slack_exporter/templates/search.js), lancés par pytest.
"use strict";
const assert = require("node:assert/strict");
const path = require("node:path");
const { normalize, parseQuery, search, highlightRanges } = require(
  path.join(__dirname, "../../src/slack_exporter/templates/search.js")
);

const INDEX = {
  conversations: [
    { id: "C1", title: "#general", type: "public" },
    { id: "D1", title: "Florentin LE MOAL", type: "im" },
  ],
  messages: [
    [0, "Alice Martin", 1700000000, "Réunion de lancement du projet Météo", "c/C1/index.html#m-1", 0],
    [0, "Bob", 1710000000, "compte rendu de la réunion", "c/C1/index.html#m-2", 1],
    [1, "Florentin LE MOAL", 1720000000, "On se voit à la REUNION demain ?", "c/D1/index.html#m-3", 0],
    [1, "Moi", 1720000100, "Oui, projet validé", "c/D1/index.html#m-4", 0],
  ],
};

const texts = (criteria) => search(INDEX, criteria).results.map((r) => r.text);
const tests = {
  "normalise accents et majuscules"() {
    assert.equal(normalize("Réunion MÉTÉO Ça"), "reunion meteo ca");
  },
  "découpe mots et expressions"() {
    assert.deepEqual(parseQuery('Réunion  "compte rendu" projet'), ["reunion", "compte rendu", "projet"]);
    assert.deepEqual(parseQuery("   "), []);
  },
  "sans accents ni majuscules, plus récents d'abord"() {
    assert.deepEqual(texts({ query: "reunion" }), [
      "On se voit à la REUNION demain ?",
      "compte rendu de la réunion",
      "Réunion de lancement du projet Météo",
    ]);
  },
  "tous les mots doivent être présents"() {
    assert.deepEqual(texts({ query: "réunion projet" }), ["Réunion de lancement du projet Météo"]);
  },
  "expression exacte"() {
    assert.deepEqual(texts({ query: '"rendu de la"' }), ["compte rendu de la réunion"]);
    assert.deepEqual(texts({ query: '"de la rendu"' }), []);
  },
  "filtre par type"() {
    assert.deepEqual(texts({ query: "reunion", type: "im" }), ["On se voit à la REUNION demain ?"]);
  },
  "filtre par personne, partiel et sans accents"() {
    assert.deepEqual(texts({ query: "reunion", author: "florentin" }), ["On se voit à la REUNION demain ?"]);
    assert.deepEqual(texts({ author: "moi" }), ["Oui, projet validé"]);
  },
  "filtre par période (bornes incluses, dates locales)"() {
    const day = (ts) => { const d = new Date(ts * 1000); return d.getFullYear() + "-" +
      String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); };
    assert.deepEqual(texts({ query: "reunion", from: day(1710000000), to: day(1710000000) }),
      ["compte rendu de la réunion"]);
  },
  "une recherche vide ne renvoie rien"() {
    assert.deepEqual(texts({ query: "" }), []);
  },
  "résultat enrichi"() {
    const [r] = search(INDEX, { query: "valide" }).results;
    assert.equal(r.conversation.title, "Florentin LE MOAL");
    assert.equal(r.author, "Moi");
    assert.equal(r.href, "c/D1/index.html#m-4");
    assert.equal(r.inThread, false);
    assert.equal(search(INDEX, { query: "compte" }).results[0].inThread, true);
  },
  "zones à surligner, sur le texte original"() {
    assert.deepEqual(highlightRanges("La Réunion, puis réunion", ["reunion"]), [[3, 10], [17, 24]]);
    assert.deepEqual(highlightRanges("aucun", ["reunion"]), []);
  },
};

let failed = 0;
for (const [name, fn] of Object.entries(tests)) {
  try { fn(); console.log("ok   " + name); }
  catch (err) { failed++; console.log("FAIL " + name + "\n" + err.message); }
}
process.exit(failed ? 1 : 0);

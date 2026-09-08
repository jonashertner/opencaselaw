/**
 * Polish pass on the nine existing redaction categories (redact.js v4).
 * No new categories — these tests pin the within-category fixes:
 *
 *   1. NAME: upper-case surnames (FR/IT pleadings), Herrn / Monsieur /
 *      Madame / Signor(a) / lower-case avv. and dott.
 *   2. NAME: trailing citation vocabulary is never swallowed
 *      ("Herr Müller Art. 41 OR" keeps "Art. 41 OR").
 *   3. ADDRESS: lower-case rue / avenue / chemin / boulevard, but only
 *      with a capitalised street name after the particle.
 *   4. DOB: "geb." + date, "Geburtsdatum:", "nata il", slash dates.
 *   5. Consistent placeholders: same original → same placeholder.
 *   6. Portability: no lookbehind, no /u flag in the module source.
 */

var assert = require('assert');
var fs = require('fs');
var r = require('../js/redact.js');
var passed = 0, failed = 0, failures = [];

function test(name, fn) {
  try { fn(); passed++; console.log('  PASS: ' + name); }
  catch (e) { failed++; failures.push(name + ': ' + (e.message || e));
              console.log('  FAIL: ' + name); console.log('        ' + (e.message || e)); }
}
function section(label) { console.log('\n' + label); }
function redacted(src) { return r.redactPII(src).redacted; }
function roundTrips(src) {
  var out = r.redactPII(src);
  assert.strictEqual(r.unredact(out.redacted, out.replacements), src, 'round-trip broke');
  return out;
}

section('1. NAME — title forms and upper-case surnames:');

test('FR pleading form "M. Jean DUPONT" is caught whole', function () {
  var out = redacted('Le défendeur, M. Jean DUPONT, conclut au rejet.');
  assert.ok(out.indexOf('DUPONT') < 0, 'upper-case surname leaked: ' + out);
  assert.ok(out.indexOf('Jean') < 0, 'first name leaked: ' + out);
});

test('IT pleading form "Sig. Mario ROSSI"', function () {
  var out = redacted('Il ricorrente Sig. Mario ROSSI chiede l\'annullamento.');
  assert.ok(out.indexOf('ROSSI') < 0 && out.indexOf('Mario') < 0, out);
});

test('Dative "Herrn Max Müller"', function () {
  var out = redacted('Die Verfügung wurde Herrn Max Müller zugestellt.');
  assert.ok(out.indexOf('Max Müller') < 0, out);
  assert.ok(out.indexOf('Herrn') < 0, 'title should be inside the span: ' + out);
});

test('Spelled-out "Monsieur Jean Dupont" and "Madame Marie Dubois"', function () {
  var out = redacted('Monsieur Jean Dupont et Madame Marie Dubois ont signé.');
  assert.ok(out.indexOf('Dupont') < 0 && out.indexOf('Dubois') < 0, out);
  assert.strictEqual(r.redactPII('Monsieur Jean Dupont et Madame Marie Dubois ont signé.').summary.byType.NAME, 2);
});

test('Spelled-out "Signora Anna Bianchi" / "Signor Luca Verdi"', function () {
  var out = redacted('La Signora Anna Bianchi e il Signor Luca Verdi.');
  assert.ok(out.indexOf('Bianchi') < 0 && out.indexOf('Verdi') < 0, out);
});

test('Lower-case Italian titles "avv. Mario Rossi" and "dott.ssa Anna Neri"', function () {
  var out = redacted('patrocinato dall\'avv. Mario Rossi e dalla dott.ssa Anna Neri');
  assert.ok(out.indexOf('Mario Rossi') < 0, 'avv. name leaked: ' + out);
  assert.ok(out.indexOf('Anna Neri') < 0, 'dott.ssa name leaked: ' + out);
});

test('Hyphenated upper-case surname "Mme Anne MARTIN-DUBOIS"', function () {
  var out = redacted('Mme Anne MARTIN-DUBOIS a recouru.');
  assert.ok(out.indexOf('MARTIN') < 0, out);
});

test('"Madame la Présidente" is not a name (lower-case article stops the match)', function () {
  assert.strictEqual(r.redactPII('Madame la Présidente, Monsieur le Juge,').summary.total, 0);
});

section('2. NAME — trailing citation vocabulary is trimmed:');

test('"Herr Müller Art. 41 OR" keeps the article reference', function () {
  var out = redacted('Wie Herr Müller Art. 41 OR geltend macht.');
  assert.ok(out.indexOf('Müller') < 0, 'name leaked: ' + out);
  assert.ok(out.indexOf('Art. 41 OR') >= 0, 'citation damaged: ' + out);
});

test('"Frau Meier BGE 143 III 480" keeps the BGE reference', function () {
  var out = redacted('Frau Meier BGE 143 III 480 zitiert.');
  assert.ok(out.indexOf('Meier') < 0, out);
  assert.ok(out.indexOf('BGE 143 III 480') >= 0, out);
});

test('"Dr. Max Müller Urteil 4A_1/2020" keeps the docket', function () {
  var out = redacted('Dr. Max Müller Urteil 4A_1/2020 vom 1. Januar 2020.');
  assert.ok(out.indexOf('Max Müller') < 0, out);
  assert.ok(out.indexOf('Urteil 4A_1/2020') >= 0, out);
});

test('Title followed only by a stop token is not a name ("Herr Art. 5")', function () {
  var out = r.redactPII('Nach Herr Art. 5 gilt nichts.');
  assert.strictEqual(out.summary.total, 0, JSON.stringify(out.replacements));
});

test('Round-trip survives trimming (offsets stay exact)', function () {
  roundTrips('Gemäss Herr Hans Meier Art. 8 BV und Frau Dr. Anna Schmid BGE 1 I 1.');
});

section('3. ADDRESS — lower-case French street words:');

test('"rue de la Gare 12" (lower-case rue)', function () {
  var out = redacted('domicilié rue de la Gare 12, 1003 Lausanne');
  assert.ok(out.indexOf('rue de la Gare 12') < 0, out);
  assert.ok(out.indexOf('[ADDRESS_1]') >= 0, out);
});

test('"avenue de l’Église 3" and "chemin des Vignes 7"', function () {
  var out = redacted('avenue de l’Église 3 puis chemin des Vignes 7');
  assert.ok(out.indexOf('Vignes 7') < 0, out);
  assert.ok(out.indexOf('Église 3') < 0, out);
});

test('Prose "mise en place depuis 3 ans" is NOT an address', function () {
  assert.strictEqual(r.redactPII('la mise en place depuis 3 ans').summary.total, 0);
});

test('"via e-mail 3" and "rue basse 3" (no capitalised street name) are NOT addresses', function () {
  assert.strictEqual(r.redactPII('envoyé via e-mail 3 fois, rue basse 3 fois').summary.total, 0);
});

test('Upper-case forms still work: "Rue du Rhône 65", "Via Pretorio 7"', function () {
  var out = redacted('Rue du Rhône 65 et Via Pretorio 7');
  assert.strictEqual(r.redactPII('Rue du Rhône 65 et Via Pretorio 7').summary.byType.ADDRESS, 2, out);
});

section('4. DOB — anchors within the existing category:');

test('"geb. 1.1.1980" (no "am")', function () {
  assert.ok(redacted('Max Müller, geb. 1.1.1980, wohnhaft').indexOf('1.1.1980') < 0);
});

test('"Geburtsdatum: 15.06.1975"', function () {
  assert.ok(redacted('Geburtsdatum: 15.06.1975').indexOf('15.06.1975') < 0);
});

test('"nata il 5.3.1962" (feminine)', function () {
  assert.ok(redacted('nata il 5.3.1962 a Lugano').indexOf('5.3.1962') < 0);
});

test('"née le 15/06/1975" (slash date)', function () {
  assert.ok(redacted('née le 15/06/1975').indexOf('15/06/1975') < 0);
});

test('"date de naissance : 15.6.1975" and "data di nascita: 5.3.1962"', function () {
  var out = redacted('date de naissance : 15.6.1975; data di nascita: 5.3.1962');
  assert.ok(out.indexOf('1975') < 0 && out.indexOf('1962') < 0, out);
});

test('Filing date "Verfügung vom 15.06.2020" is still NOT a DOB', function () {
  assert.strictEqual(r.redactPII('Verfügung vom 15.06.2020 und Eingabe vom 1/2/2021').summary.total, 0);
});

section('5. Consistent placeholders:');

test('Same party three times → one placeholder, three occurrences', function () {
  var src = 'Herr Max Müller klagt. Herr Max Müller verlangt. Herr Max Müller obsiegt.';
  var out = roundTrips(src);
  assert.strictEqual(out.summary.byType.NAME, 3);
  assert.strictEqual(out.redacted, '[NAME_1] klagt. [NAME_1] verlangt. [NAME_1] obsiegt.');
  assert.ok(out.redacted.indexOf('[NAME_2]') < 0);
});

test('Different parties keep distinct placeholders', function () {
  var out = roundTrips('Herr Max Müller gegen Frau Anna Schmid, dann wieder Herr Max Müller.');
  assert.strictEqual(out.redacted, '[NAME_1] gegen [NAME_2], dann wieder [NAME_1].');
});

test('Same email twice shares a placeholder; a different one does not', function () {
  var out = roundTrips('a@x.ch, a@x.ch, b@x.ch');
  assert.strictEqual(out.redacted, '[EMAIL_1], [EMAIL_1], [EMAIL_2]');
  assert.strictEqual(out.summary.byType.EMAIL, 3);
});

test('Placeholders are per type: same digits as AHV and inside a phone are unrelated', function () {
  var out = roundTrips('AHV 756.1234.5678.90 und AHV 756.1234.5678.90');
  assert.strictEqual(out.redacted, 'AHV [AHV_1] und AHV [AHV_1]');
});

section('6. Portability of the module source:');

test('No lookbehind and no /u flag anywhere in redact.js', function () {
  var src = fs.readFileSync(__dirname + '/../js/redact.js', 'utf8');
  assert.ok(src.indexOf('(?<') < 0, 'lookbehind found');
  /* A regex literal's flags sit between the closing slash and , ; or ). */
  assert.ok(!/\/[gimsy]*u[gimsy]*[,;)]/.test(src), '/u flag found');
  r.PATTERNS.forEach(function (p) { assert.ok(p.regex.flags.indexOf('u') < 0, p.type + ' uses /u'); });
});

test('Still exactly nine categories', function () {
  assert.deepStrictEqual(Object.keys(r.PII_TYPES).sort(),
    ['ADDRESS', 'AHV', 'CHE', 'DOB', 'EMAIL', 'IBAN', 'NAME', 'PHONE', 'POSTAL']);
});

console.log('\n' + '='.repeat(50));
console.log('Polish results: ' + passed + ' passed, ' + failed + ' failed');
console.log('='.repeat(50));
if (failed) { console.log('\nFailures:'); failures.forEach(function (f) { console.log('  - ' + f); }); process.exit(1); }

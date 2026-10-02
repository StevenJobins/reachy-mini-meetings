// Voice commands from the room, found in the finished captions. Pure logic: no DOM, no three.js.
//
// "Reachy, volume 5" in any language: wake word + volume word + number 0-10 (5 = 50 %).
// A number 11-100 counts as percent ("Reachy, volume 70" = 70 %). The caption's original text AND its
// translation are checked, so a language without its own entries below still works through the
// translation (DeepL keeps "Reachy" and translates "volume" and the number).

// Whisper writes the robot's name in many ways, depending on the language it hears.
const WAKE = [
  /(?<!\p{L})(?:reachy|reachie|reechy|reachi|richie|ritchie|ritchy|richy|ritschi|ritschie|rici|ricci|richi|rietschi)(?!\p{L})/u,
  // Fuzzy: r + ea/ee/ie/i/e + (t)(s)ch + vowel: Reechie, Ritchy, Rietschi, Rischi, Reachey, ...
  /(?<!\p{L})r(?:ea|ee|ie|i|e)t?s?c?h(?:y|ie|i|ey|e)(?!\p{L})/u,
  /(?<!\p{L})(?:ричи|рич|річі)(?!\p{L})/u,
  /リーチー|リーチ|里奇|瑞奇|锐奇|銳奇|리치|리취|ريتشي|ريشي|रीची|रिची/u,
];

// "volume" in many languages (lower case). Latin/Cyrillic need word boundaries, CJK etc. do not.
const VOLUME_WORDS = [
  "volume", "volumen", "volym", "volum", "lautstärke", "lautstaerke", "lydstyrke", "ljudvolym", "geluid",
  "głośność", "glosnosc", "hlasitost", "hlasitosť", "hangerő", "äänenvoimakkuus", "ses", "sesi", "ses seviyesi",
  "volumul", "jačina", "glasnost", "громкость", "гучність", "звук", "ένταση", "âm lượng", "kelantangan",
];
const VOLUME_CJK = ["音量", "ボリューム", "볼륨", "음량", "مستوى الصوت", "الصوت", "صوت", "आवाज़", "आवाज", "वॉल्यूम", "ระดับเสียง"];

// Number words 0-10 (lower case) -> value.
const NUMBERS = {};
const addNums = (words) => words.split(" ").forEach((w, i) => { if (w !== "-") for (const v of w.split("/")) NUMBERS[v] = i; });
addNums("zero one two three four five six seven eight nine ten");                        // en
addNums("null eins/ein/eine zwei drei vier fünf/fuenf sechs sieben acht neun zehn");     // de
addNums("zéro un/une deux trois quatre cinq six sept huit neuf dix");                    // fr
addNums("zero uno/una due tre quattro cinque sei sette otto nove dieci");                // it
addNums("cero uno/una dos tres cuatro cinco seis siete ocho nueve diez");                // es
addNums("zero uma dois/duas três/tres quatro cinco seis sete oito nove dez");            // pt
addNums("nul een twee drie vier vijf zes zeven acht negen tien");                        // nl
addNums("noll ett två tre fyra fem sex sju åtta nio tio");                               // sv
addNums("null ett - tre fire fem seks syv/sju åtte/otte - ti");                          // no/da
addNums("zero jeden/jedna dwa trzy cztery pięć sześć siedem osiem dziewięć dziesięć");   // pl
addNums("nula jedna/jeden dva tři čtyři pět šest sedm osm devět deset");                 // cs
addNums("sıfır bir iki üç dört beş altı yedi sekiz dokuz -");                            // tr
addNums("ноль один/одна два/две три четыре пять шесть семь восемь девять десять");       // ru
addNums("нуль один/одна два/дві три чотири п'ять/пʼять шість сім вісім дев'ять десять"); // uk
addNums("μηδέν ένα δύο τρία τέσσερα πέντε έξι επτά/εφτά οκτώ/οχτώ εννέα/εννιά δέκα");   // el
addNums("영 하나 둘 셋 넷 다섯 여섯 일곱 여덟 아홉 열/십");                              // ko (native)
// Left out on purpose, they are everyday words elsewhere: "to"/"en" (no/da/sv), "um" (pt, German "um"),
// "on" (tr), "ni" (no), Korean single syllables 일/이/사/오. Those languages still work with digits
// (Whisper usually writes digits) or through the translation.
const CJK_DIGITS = { "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10 };

function normalize(s) {
  return s.normalize("NFKC").toLowerCase()
    // Arabic-Indic and Devanagari digits -> 0-9
    .replace(/[٠-٩]/g, (d) => String(d.charCodeAt(0) - 0x660))
    .replace(/[०-९]/g, (d) => String(d.charCodeAt(0) - 0x966));
}

/** Position right after the volume word, or -1. */
function volumeEnd(t) {
  let best = -1;
  for (const w of VOLUME_WORDS) {
    const m = new RegExp(`(?<!\\p{L})${w}(?!\\p{L})`, "u").exec(t);
    if (m && (best < 0 || m.index < best.index)) best = { index: m.index, end: m.index + w.length };
  }
  for (const w of VOLUME_CJK) {
    const i = t.indexOf(w);
    if (i >= 0 && (best < 0 || i < best.index)) best = { index: i, end: i + w.length };
  }
  return best < 0 ? -1 : best.end;
}

const toPercent = (n, pct) => (!pct && n <= 10 ? Math.round(n * 10) : n <= 100 ? Math.round(n) : null);

/** First number in s -> percent 0-100, or null. Digits first: Whisper usually writes numbers as digits. */
function firstNumber(s) {
  const d = /(\d+(?:[.,]\d+)?)\s*(%)?/.exec(s);   // also when glued to other characters ("を5にして")
  if (d) {
    const p = toPercent(Number(d[1].replace(",", ".")), !!d[2]);
    if (p !== null) return p;
  }
  const tokens = s.split(/[^\p{L}\p{N}'ʼ.,%]+/u).filter(Boolean);
  for (const raw of tokens) {
    const tok = raw.replace(/[.,]$/, "");
    if (tok in NUMBERS) return NUMBERS[tok] * 10;
    // CJK: number characters may be glued to other characters ("调到五")
    for (const ch of tok) if (ch in CJK_DIGITS) return CJK_DIGITS[ch] * 10;
  }
  return null;
}

/** Why a text with a volume word is not a command (for the log), or null. */
export function explainVolume(text) {
  if (!text) return null;
  const t = normalize(text);
  if (volumeEnd(t) < 0) return null;
  if (parseVolume(text) !== null) return null;
  if (!WAKE.some((re) => re.test(t)) && !isShortCommand(t)) return "no 'Reachy' heard";
  return "no number 0-10 after the volume word";
}

// "Lautstärke 9" alone (no name, at most 4 words) also counts: Whisper sometimes mangles the name.
function isShortCommand(t) {
  return t.split(/[^\p{L}\p{N}%]+/u).filter(Boolean).length <= 4;
}

/** One text -> volume percent (0-100) if it is "Reachy, volume N", else null. */
export function parseVolume(text) {
  if (!text) return null;
  const t = normalize(text);
  const end = volumeEnd(t);
  if (end < 0) return null;
  if (!WAKE.some((re) => re.test(t)) && !isShortCommand(t)) return null;
  return firstNumber(t.slice(end));
}

/** A caption ({text, translation}) -> volume percent or null. Original first, then the translation. */
export function volumeCommand(caption) {
  return parseVolume(caption.text) ?? parseVolume(caption.translation);
}

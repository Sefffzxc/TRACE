const NAME_WORD = /\p{L}[\p{L}\p{M}]*/gu;
const ROMAN_SUFFIX = /^(?:II|III|IV|V|VI|VII|VIII|IX|X)$/i;
const MIXED_CASE_NAME = /^(?:Mc|Mac|DeLa|DeLos|DeLas)\p{Lu}\p{Ll}+$/u;

/** Format a person's display name without changing their saved/input value. */
export function formatPersonName(value) {
  if (value == null) return '';

  const name = String(value).normalize('NFC').trim().replace(/\s+/gu, ' ');
  return name.replace(NAME_WORD, (word, offset) => {
    const isSuffix = offset > 0 && !/\p{L}/u.test(name.slice(offset + word.length));
    if (isSuffix && ROMAN_SUFFIX.test(word)) return word.toUpperCase();
    // Keep intentional spellings such as McDonald, MacArthur and DeLaCruz.
    if (MIXED_CASE_NAME.test(word)) return word;

    const lower = word.toLowerCase();
    return lower.replace(/^\p{L}/u, (initial) => initial.toUpperCase());
  });
}

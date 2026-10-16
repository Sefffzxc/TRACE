/**
 * The app was called LBCA before it was TRACE, and its browser storage keys
 * carried that name. Renaming them outright would sign every user out and
 * forget their theme and sidebar choice, so on start-up any value still
 * stored under an old `lbca.` key is moved to its `trace.` key — once, before
 * anything reads it.
 *
 * Safe to delete once every browser that used the old name has loaded the
 * app at least once.
 */
const OLD_PREFIX = 'lbca.';
const NEW_PREFIX = 'trace.';

export function migrateStorageKeys() {
  try {
    const oldKeys = [];
    for (let i = 0; i < localStorage.length; i += 1) {
      const key = localStorage.key(i);
      if (key && key.startsWith(OLD_PREFIX)) oldKeys.push(key);
    }
    oldKeys.forEach((key) => {
      const renamed = NEW_PREFIX + key.slice(OLD_PREFIX.length);
      if (localStorage.getItem(renamed) === null) {
        localStorage.setItem(renamed, localStorage.getItem(key));
      }
      localStorage.removeItem(key);
    });
  } catch {
    // Storage blocked (private window, disabled site data): nothing to move.
  }
}
